'use client'

/**
 * /refunds — employee expense-refund workflow (CFO 2026-07-13). One page, three
 * roles: everyone can submit + see their own; senior accountants get a "to
 * process" queue (upload FNB proof / reject); the CFO gets an "approve" queue.
 * Replaces the ~10-email chain.
 */

import { useEffect, useState, useCallback, useRef, Fragment } from 'react'
import { apiFetch, apiFetchRaw, API_BASE, saveBlob } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { TableWrapper, Table, TableHead, TableBody, TableRow, TableHeader, TableCell } from '@/components/ui/table'
import { useTheme } from '@/contexts/ThemeContext'
import { formatCurrency, formatDate, sumAmounts } from '@/lib/utils'
import type { Theme } from '@/lib/themes'
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts'
import {
  Receipt, Upload, CheckCircle2, XCircle, Clock, Paperclip, Sparkles, BadgeCheck,
  Wallet, AlertTriangle, type LucideIcon,
} from 'lucide-react'

interface Claim {
  id: string; requester: string; expense_date: string; category: string
  amount: string; currency: string; description: string; status: string; status_label: string
  approver: string | null; reject_reason: string; invoice_count: number; has_payment_proof: boolean
  paid_by?: string; paid_at?: string | null; paid_reference?: string; paid_note?: string
  gl_account_label: string | null
  // Timestamps already returned by the API (used only to DERIVE an age badge —
  // no data change). Optional here because the serializer may omit some.
  submitted_at?: string | null; created_at?: string
}
interface Approver { id: number; name: string }
interface Acct { id: string; code: string; name: string }

// ─── Presentation helpers (derive-only — never mutate a figure) ───────────────

// A claim is "open" while it is still in flight: submitted (with the accountant),
// pending_cfo (with the CFO), or approved (awaiting payment). Not paid/rejected/draft.
const OPEN_STATUSES: string[] = ['submitted', 'pending_cfo', 'approved']
// Donut render order — semantic, matches the workflow left→right.
const STATUS_SEQ: string[] = ['submitted', 'pending_cfo', 'approved', 'paid', 'rejected', 'draft']

const STATUS_VARIANT: Record<string, 'info' | 'warning' | 'success' | 'danger' | 'muted' | 'default'> = {
  submitted: 'info', pending_cfo: 'warning', approved: 'success',
  paid: 'success', rejected: 'danger', draft: 'muted',
}

function statusColor(theme: Theme, status: string): string {
  switch (status) {
    case 'submitted': return theme.inf
    case 'pending_cfo': return theme.wr
    case 'approved': return theme.teal
    case 'paid': return theme.ok
    case 'rejected': return theme.er
    default: return theme.t3 // draft
  }
}

/** Whole days since the claim entered the workflow (submitted_at, else created_at). */
function claimAgeDays(c: Claim): number | null {
  const raw = c.submitted_at || c.created_at
  if (!raw) return null
  const t = new Date(raw).getTime()
  if (isNaN(t)) return null
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000))
}
/** Sort key — earliest (oldest) first; unknown dates sink to the bottom. */
function claimTs(c: Claim): number {
  const raw = c.submitted_at || c.created_at
  const t = raw ? new Date(raw).getTime() : NaN
  return isNaN(t) ? Infinity : t
}

/** Age chip with an SLA heat colour for open claims (older = hotter). */
function ageBadge(theme: Theme, c: Claim) {
  const d = claimAgeDays(c)
  if (d === null) return <span className="text-xs" style={{ color: theme.t3 }}>—</span>
  let bg = theme.g100, fg = theme.t2
  if (OPEN_STATUSES.includes(c.status)) {
    if (d >= 21) { bg = theme.erB; fg = theme.er }
    else if (d >= 10) { bg = theme.wrB; fg = theme.wr }
    else if (d >= 4) { bg = theme.inB; fg = theme.inf }
    else { bg = theme.okB; fg = theme.ok }
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums"
          style={{ background: bg, color: fg }}>{d}d</span>
  )
}

// ─── Count-up hook (mirrors the dashboard KPI pattern) ────────────────────────
// Purely presentational: animates 0 → the REAL value once on mount. Snaps to the
// final value under reduce-motion, and always lands exactly on the true number.
function useCountUp(target: number, durationMs = 900, enabled = true): number {
  const [val, setVal] = useState(enabled ? 0 : target)
  const rafRef = useRef<number | null>(null)
  useEffect(() => {
    if (!enabled || !isFinite(target) || target === 0) { setVal(target); return }
    let start: number | null = null
    const tick = (t: number) => {
      if (start === null) start = t
      const p = Math.min(1, (t - start) / durationMs)
      const eased = 1 - Math.pow(1 - p, 3) // ease-out cubic
      setVal(target * eased)
      if (p < 1) rafRef.current = requestAnimationFrame(tick)
      else setVal(target)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [target, durationMs, enabled])
  return val
}

// ─── KPI tile (local copy of the dashboard GlassKpiCard pattern) ──────────────
interface KpiTileProps {
  label: string; sub: string; icon: LucideIcon; raw: number
  format: (v: number) => string; accent: string
  theme: Theme; reduceMotion: boolean; delay: number
}
function KpiTile({ label, sub, icon: Icon, raw, format, accent, theme, reduceMotion, delay }: KpiTileProps) {
  const animate = isFinite(raw) && raw !== 0 && !reduceMotion
  const counted = useCountUp(raw, 900, animate)
  const shown = format(animate ? counted : raw)
  return (
    <div
      className={'glass-card relative overflow-hidden p-4 flex flex-col' + (reduceMotion ? '' : ' refund-anim')}
      style={{ minHeight: 112, animationDelay: reduceMotion ? undefined : `${delay}ms` }}
    >
      <span aria-hidden className="absolute top-0 left-0 right-0 h-[2px]"
            style={{ background: `linear-gradient(90deg, ${accent}, transparent 82%)`, opacity: 0.8 }} />
      <div className="flex items-center gap-2.5">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
             style={{ background: accent + '1A' }}>
          <Icon className="w-[18px] h-[18px]" style={{ color: accent }} strokeWidth={1.7} />
        </div>
        <span className="text-[11px] font-semibold uppercase tracking-[0.1em] leading-tight"
              style={{ color: theme.t2 }}>{label}</span>
      </div>
      <div className="font-display-tight font-bold mt-3 tabular-nums whitespace-nowrap"
           style={{ color: theme.navy, fontSize: 'clamp(18px, 1.7vw, 28px)' }}>{shown}</div>
      <div className="text-xs mt-1" style={{ color: theme.t3 }}>{sub}</div>
    </div>
  )
}

export default function RefundsPage() {
  const { theme, reduceMotion } = useTheme()
  const [mine, setMine] = useState<Claim[]>([])
  const [approvers, setApprovers] = useState<Approver[]>([])
  const [queue, setQueue] = useState<Claim[] | null>(null)     // null = not an accountant
  const [cfoQueue, setCfoQueue] = useState<Claim[] | null>(null) // null = not CFO
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  // accountant "process" modal — upload proof + link a GL account
  const [processFor, setProcessFor] = useState<Claim | null>(null)
  const proofRef = useRef<HTMLInputElement>(null)
  const [glQuery, setGlQuery] = useState('')
  const [glOpts, setGlOpts] = useState<Acct[]>([])
  const [glSel, setGlSel] = useState<Acct | null>(null)
  const [glSuggesting, setGlSuggesting] = useState(false)
  useEffect(() => {
    if (!processFor || glQuery.trim().length < 2) { setGlOpts([]); return }
    let live = true
    // EXPENSE accounts only — no balance-sheet / revenue / reinsurance
    apiFetch<Acct[]>(`/expense-claims/gl-accounts/?search=${encodeURIComponent(glQuery)}`)
      .then(r => { if (live) setGlOpts(r || []) }).catch(() => {})
    return () => { live = false }
  }, [glQuery, processFor])

  async function suggestGl() {
    if (!processFor) return
    setGlSuggesting(true)
    try {
      const r = await apiFetch<{ suggestions: (Acct & { reason: string })[] }>(
        '/expense-claims/suggest-gl/', { method: 'POST', body: JSON.stringify({ description: `${processFor.category} ${processFor.description}` }) })
      setGlOpts((r.suggestions || []).filter(s => s.id) as Acct[])
    } finally { setGlSuggesting(false) }
  }

  // submit form
  const [amount, setAmount] = useState('')
  const [category, setCategory] = useState('')
  const [description, setDescription] = useState('')
  const [approverId, setApproverId] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const words = description.trim() ? description.trim().split(/\s+/).length : 0

  const load = useCallback(async () => {
    setMine(await apiFetch<Claim[]>('/expense-claims/mine/').catch(() => []))
    setApprovers(await apiFetch<Approver[]>('/expense-claims/approvers/').catch(() => []))
    try { setQueue(await apiFetch<Claim[]>('/expense-claims/queue/')) } catch { setQueue(null) }
    try { setCfoQueue(await apiFetch<Claim[]>('/expense-claims/cfo-queue/')) } catch { setCfoQueue(null) }
  }, [])
  useEffect(() => { load() }, [load])

  async function submit() {
    setBusy(true); setMsg(null)
    try {
      const fd = new FormData()
      fd.append('amount', amount); fd.append('category', category)
      fd.append('description', description); fd.append('approver_id', approverId)
      Array.from(fileRef.current?.files || []).forEach(f => fd.append('invoices', f))
      await apiFetch('/expense-claims/', { method: 'POST', body: fd })
      setMsg('Submitted — it is with the accountant now.')
      setAmount(''); setCategory(''); setDescription(''); setApproverId('')
      if (fileRef.current) fileRef.current.value = ''
      load()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }

  async function act(url: string, opts?: RequestInit) {
    setBusy(true)
    try { await apiFetch(url, opts); load() }
    catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }
  const reject = (id: string) => {
    // A rejection goes back to the requester. Two things caught staff out
    // (CFO 2026-08-26): a claim that was ALREADY PAID was rejected instead of
    // closed out, and a fuel claim was rejected for "no invoice" when a receipt
    // was attached. Guide the reviewer away from both before they type a reason.
    const reason = window.prompt(
      'Reason (sent back to the requester).\n\n'
      + '• If this refund was ALREADY PAID, press Cancel and use "Already paid" instead of rejecting.\n'
      + '• A till slip or receipt counts as proof — do not reject for "no invoice".')
    if (reason) act(`/expense-claims/${id}/reject/`, { method: 'POST', body: JSON.stringify({ reason }) })
  }
  const approve = (id: string) => act(`/expense-claims/${id}/approve/`, { method: 'POST', body: JSON.stringify({}) })
  // Close out a refund that HAS been paid, instead of rejecting it with the
  // reason typed "PAID" (Bharath Balasubramanian 2026-08-17). A reference is
  // required — recording money as paid with no evidence is the gap this closes.
  const markPaid = (id: string) => {
    const reference = window.prompt(
      'Payment reference — the FNB reference, or the payroll period it went out on.\n'
      + 'This is the evidence that the refund was actually paid.')
    if (reference === null) return
    if (!reference.trim()) { setMsg('A payment reference is needed to mark a refund paid.'); return }
    const note = window.prompt('How was it paid? (optional)') || ''
    act(`/expense-claims/${id}/mark-paid/`,
        { method: 'POST', body: JSON.stringify({ reference: reference.trim(), note }) })
  }
  async function doProcess() {
    if (!processFor || !proofRef.current?.files?.[0]) return
    const fd = new FormData()
    fd.append('payment_proof', proofRef.current.files[0])
    if (glSel) fd.append('gl_account_id', glSel.id)
    await act(`/expense-claims/${processFor.id}/process/`, { method: 'POST', body: fd })
    setProcessFor(null); setGlSel(null); setGlQuery(''); setGlOpts([])
  }

  // Files are fetched through the authenticated endpoint (raw /media/ URLs 404
  // in production) and opened as a blob — same pattern as /spend-requests.
  async function openFile(id: string, kind: 'proof' | 'invoice', i = 0) {
    try {
      // apiFetchRaw does NOT prefix /api/v1 (it's for non-API paths) — this IS an
      // API endpoint, so prefix API_BASE. Without it the request hits the Next
      // frontend and returns the app's HTML shell (the "Initialising
      // authentication…" splash), which then opened as the "invoice". (Keetile, 2026-07-16)
      const res = await apiFetchRaw(`${API_BASE}/expense-claims/${id}/file/?kind=${kind}&i=${i}`)
      if (!res.ok) { setMsg('Could not open the file.'); return }
      // Save via a download anchor, NOT window.open(blob) — the latter silently
      // fails inside the OmniDesktop wrapper (2026-07-28). See api.saveBlob.
      saveBlob(await res.blob(), `${kind}-${i + 1}`)
    } catch { setMsg('Could not open the file.') }
  }

  // ── Derived brief (same data already fetched, deduped across the queues) ────
  const byId = new Map<string, Claim>()
  for (const c of [...mine, ...(queue || []), ...(cfoQueue || [])]) if (!byId.has(c.id)) byId.set(c.id, c)
  const all = Array.from(byId.values())
  const openClaims = all.filter(c => OPEN_STATUSES.includes(c.status))
  const pendingCount = all.filter(c => c.status === 'submitted' || c.status === 'pending_cfo').length
  const toProcessCount = all.filter(c => c.status === 'approved').length
  const openValueNum = sumAmounts(openClaims.map(c => c.amount))
  const openCurrency = openClaims[0]?.currency || 'BWP'
  const oldestDays = openClaims.reduce((m, c) => { const d = claimAgeDays(c); return d !== null && d > m ? d : m }, 0)
  const donut = STATUS_SEQ.map(s => ({
    status: s,
    label: all.find(c => c.status === s)?.status_label || s.replace(/_/g, ' '),
    value: all.filter(c => c.status === s).length,
    color: statusColor(theme, s),
  })).filter(x => x.value > 0)

  // ── One ranked claim table, reused by all three queues ──────────────────────
  const renderClaims = (
    claims: Claim[],
    opts: {
      rankByAge?: boolean
      emptyMsg: string
      actions?: (c: Claim) => React.ReactNode
      extraDocs?: (c: Claim) => React.ReactNode
    },
  ) => {
    const cols = 6 + (opts.actions ? 1 : 0)
    const rows = opts.rankByAge ? [...claims].sort((a, b) => claimTs(a) - claimTs(b)) : claims
    if (rows.length === 0) {
      return <p className="text-sm px-1 py-1.5" style={{ color: theme.t3 }}>{opts.emptyMsg}</p>
    }
    return (
      <TableWrapper>
        <Table>
          <TableHead>
            <tr>
              <TableHeader>Age</TableHeader>
              <TableHeader>Claim</TableHeader>
              <TableHeader>Requester</TableHeader>
              <TableHeader className="text-right">Amount</TableHeader>
              <TableHeader>Docs</TableHeader>
              <TableHeader>Status</TableHeader>
              {opts.actions && <TableHeader className="text-right">Action</TableHeader>}
            </tr>
          </TableHead>
          <TableBody>
            {rows.map(c => (
              <Fragment key={c.id}>
                <TableRow>
                  <TableCell>{ageBadge(theme, c)}</TableCell>
                  <TableCell>
                    <div className="font-medium" style={{ color: theme.text }}>{c.category || 'Refund'}</div>
                    <div className="text-xs" style={{ color: theme.t3 }}>
                      {formatDate(c.expense_date)}{c.gl_account_label ? ` · GL ${c.gl_account_label}` : ''}
                    </div>
                  </TableCell>
                  <TableCell><span className="text-sm" style={{ color: theme.t2 }}>{c.requester}</span></TableCell>
                  {/* Amount rendered byte-identical to the original row: `{currency} {amount}` */}
                  <TableCell className="text-right tabular-nums font-semibold" style={{ color: theme.navy }}>
                    {c.currency} {c.amount}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      {c.invoice_count > 0
                        ? Array.from({ length: c.invoice_count }, (_, i) => (
                          <button key={i} onClick={() => openFile(c.id, 'invoice', i)}
                                  className="inline-flex items-center gap-0.5 text-xs underline" style={{ color: theme.inf }}>
                            <Paperclip className="h-3 w-3" />receipt {c.invoice_count > 1 ? i + 1 : ''}
                          </button>
                        ))
                        : (!opts.extraDocs && <span className="text-xs" style={{ color: theme.t3 }}>—</span>)}
                      {opts.extraDocs?.(c)}
                    </div>
                  </TableCell>
                  <TableCell><Badge variant={STATUS_VARIANT[c.status] || 'default'}>{c.status_label}</Badge></TableCell>
                  {opts.actions && <TableCell className="text-right">{opts.actions(c)}</TableCell>}
                </TableRow>
                {c.reject_reason && c.status === 'rejected' && (
                  <TableRow>
                    <TableCell colSpan={cols} className="py-1.5 text-xs" style={{ color: theme.er, background: theme.erB }}>
                      ↩ {c.reject_reason}
                    </TableCell>
                  </TableRow>
                )}
                {/* A paid refund must SHOW its evidence — that is the whole point. */}
                {c.status === 'paid' && (c.paid_reference || c.paid_by) && (
                  <TableRow>
                    <TableCell colSpan={cols} className="py-1.5 text-xs" style={{ color: theme.ok, background: theme.okB }}>
                      ✓ paid{c.paid_reference ? ` · ref ${c.paid_reference}` : ''}
                      {c.paid_by ? ` · recorded by ${c.paid_by}` : ''}
                      {c.paid_at ? ` · ${c.paid_at.slice(0, 10)}` : ''}
                      {c.paid_note ? ` · ${c.paid_note}` : ''}
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </TableWrapper>
    )
  }

  return (
    <div>
      <style>{`
        @keyframes refund-rise { from { opacity: 0; transform: translateY(8px) } to { opacity: 1; transform: none } }
        .refund-anim { animation: refund-rise 360ms cubic-bezier(0.16, 1, 0.3, 1) both }
        @media (prefers-reduced-motion: reduce) { .refund-anim { animation: none !important } }
      `}</style>
      <TopBar />
      <div className="p-6 max-w-6xl mx-auto space-y-6" style={{ color: theme.text }}>
        <div>
          <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.11em]" style={{ color: theme.orangeText }}>
            <span className="w-[5px] h-[5px] rounded-full" style={{ background: theme.orange }} /> Spend &amp; payments
          </div>
          <h1 className="font-display-tight text-[25px] font-bold flex items-center gap-2.5 mt-1.5 tracking-[-0.03em]" style={{ color: theme.navy }}>
            <Receipt className="h-6 w-6" style={{ color: theme.orange }} /> Refunds
          </h1>
          <p className="text-sm mt-1.5" style={{ color: theme.t2 }}>Claim back an expense — attach the receipts, pick a senior accountant, submit. No more email chains.</p>
        </div>
        {msg && <div className="text-sm rounded-md p-3" style={{ background: theme.g100, color: theme.text }}>{msg}</div>}

        {/* Brief — derived entirely from the claims already fetched */}
        <section aria-label="Refund overview" className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
          <KpiTile label="Pending" sub="With accountant / CFO" icon={Clock}
                   raw={pendingCount} format={v => String(Math.round(v))}
                   accent={theme.orange} theme={theme} reduceMotion={reduceMotion} delay={0} />
          <KpiTile label="To process" sub="Approved, awaiting payment" icon={CheckCircle2}
                   raw={toProcessCount} format={v => String(Math.round(v))}
                   accent={theme.inf} theme={theme} reduceMotion={reduceMotion} delay={60} />
          <KpiTile label="Open value" sub={`${openClaims.length} open claim${openClaims.length === 1 ? '' : 's'}`} icon={Wallet}
                   raw={openValueNum} format={v => formatCurrency(v, openCurrency)}
                   accent={theme.teal} theme={theme} reduceMotion={reduceMotion} delay={120} />
          <KpiTile label="Oldest" sub={openClaims.length ? 'days — longest open' : 'nothing waiting'} icon={AlertTriangle}
                   raw={oldestDays} format={v => `${Math.round(v)}d`}
                   accent={theme.wr} theme={theme} reduceMotion={reduceMotion} delay={180} />
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Submit */}
          <Card className="lg:col-span-8">
            <CardHeader><CardTitle className="font-display">New refund request</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="grid sm:grid-cols-3 gap-3">
                <input value={amount} onChange={e => setAmount(e.target.value)} placeholder="Amount (BWP)" className="px-3 py-2 text-sm border rounded-md bg-background" />
                <input value={category} onChange={e => setCategory(e.target.value)} placeholder="Category (Starlink, travel…)" className="px-3 py-2 text-sm border rounded-md bg-background" />
                <select value={approverId} onChange={e => setApproverId(e.target.value)} aria-label="Approver" className="px-3 py-2 text-sm border rounded-md bg-background">
                  <option value="">Send to… (senior accountant)</option>
                  {approvers.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
              {approvers.length === 0 && (
                <p className="text-xs text-amber-600">Couldn&apos;t load the senior-accountant list — refresh the page, or sign in again if it stays empty.</p>
              )}
              <textarea value={description} onChange={e => setDescription(e.target.value)} rows={4}
                placeholder="Describe the expense in your own words (at least 50 words) — what it was for, when, why."
                className="w-full px-3 py-2 text-sm border rounded-md bg-background" />
              <div className="flex flex-wrap items-center gap-3">
                <input ref={fileRef} type="file" multiple accept="image/*,.pdf" aria-label="Attach receipts (images or PDF)" className="text-sm" />
                <span className={'text-xs ' + (words >= 50 ? 'text-emerald-600' : 'text-muted-foreground')}>{words}/50 words</span>
                <Button size="sm" disabled={busy || words < 50 || !amount || !approverId} onClick={submit}>
                  <Upload className="h-4 w-4 mr-1" /> Submit refund
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Status mix — recharts donut over the same claims */}
          <Card className="lg:col-span-4">
            <CardHeader><CardTitle className="font-display">Status mix</CardTitle></CardHeader>
            <CardContent>
              {all.length === 0 ? (
                <p className="text-sm" style={{ color: theme.t3 }}>No refunds yet.</p>
              ) : (
                <div className="flex items-center gap-5">
                  <div className="relative flex-shrink-0" style={{ width: 132, height: 132 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={donut} dataKey="value" nameKey="label" cx="50%" cy="50%"
                             innerRadius={42} outerRadius={62} paddingAngle={2} stroke="none"
                             isAnimationActive={!reduceMotion}>
                          {donut.map((d, i) => <Cell key={i} fill={d.color} />)}
                        </Pie>
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                      <span className="font-display-tight font-bold tabular-nums" style={{ fontSize: 22, color: theme.navy }}>{all.length}</span>
                      <span className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>claims</span>
                    </div>
                  </div>
                  <ul className="flex-1 min-w-0 space-y-1.5">
                    {donut.map(d => (
                      <li key={d.status} className="flex items-center gap-2 text-sm">
                        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: d.color }} />
                        <span className="flex-1 truncate" style={{ color: theme.t2 }}>{d.label}</span>
                        <span className="tabular-nums font-semibold" style={{ color: theme.navy }}>{d.value}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Accountant queue */}
        {queue && (
          <Card>
            <CardHeader className="flex items-center justify-between gap-2">
              <CardTitle className="font-display flex items-center gap-2"><Upload className="h-4 w-4" style={{ color: theme.inf }} /> Payments to load</CardTitle>
              <Badge variant="info">{queue.length}</Badge>
            </CardHeader>
            <CardContent>
              <p className="text-xs mb-3" style={{ color: theme.t3 }}>Load the payment in FNB, then upload proof. Longest-waiting first.</p>
              {renderClaims(queue, {
                rankByAge: true,
                emptyMsg: 'Nothing waiting.',
                actions: c => (
                  <div className="flex flex-wrap gap-1.5 justify-end">
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => { setProcessFor(c); setGlSel(null); setGlQuery('') }}><Upload className="h-4 w-4 mr-1" />Process</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => markPaid(c.id)} title="Already paid outside Omni — record it as paid instead of rejecting it"><BadgeCheck className="h-4 w-4 mr-1" />Already paid</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => reject(c.id)}><XCircle className="h-4 w-4 mr-1" />Reject</Button>
                  </div>
                ),
              })}
            </CardContent>
          </Card>
        )}

        {/* CFO queue */}
        {cfoQueue && (
          <Card>
            <CardHeader className="flex items-center justify-between gap-2">
              <CardTitle className="font-display flex items-center gap-2"><Clock className="h-4 w-4" style={{ color: theme.wr }} /> Pending your approval</CardTitle>
              <Badge variant="warning">{cfoQueue.length}</Badge>
            </CardHeader>
            <CardContent>
              {renderClaims(cfoQueue, {
                rankByAge: true,
                emptyMsg: 'Nothing pending.',
                extraDocs: c => (c.has_payment_proof
                  ? <button onClick={() => openFile(c.id, 'proof')} className="text-xs underline" style={{ color: theme.inf }}>proof</button>
                  : null),
                actions: c => (
                  <div className="flex flex-wrap gap-1.5 justify-end">
                    <Button size="sm" disabled={busy} onClick={() => approve(c.id)}><CheckCircle2 className="h-4 w-4 mr-1" />Approve</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => markPaid(c.id)} title="Already paid outside Omni — record it as paid instead of rejecting it"><BadgeCheck className="h-4 w-4 mr-1" />Already paid</Button>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => reject(c.id)}><XCircle className="h-4 w-4 mr-1" />Reject</Button>
                  </div>
                ),
              })}
            </CardContent>
          </Card>
        )}

        {/* My refunds */}
        <Card>
          <CardHeader><CardTitle className="font-display">My refunds</CardTitle></CardHeader>
          <CardContent>
            {renderClaims(mine, { emptyMsg: 'No requests yet.' })}
          </CardContent>
        </Card>
      </div>

      {/* Accountant process modal — upload FNB proof + link the GL account */}
      {processFor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={() => setProcessFor(null)}>
          <Card className="w-full max-w-md" onClick={e => e.stopPropagation()}><CardContent className="p-5 space-y-3">
            <div className="flex items-center justify-between">
              <div className="font-medium">Process refund — {processFor.currency} {processFor.amount}</div>
              <button onClick={() => setProcessFor(null)}><XCircle className="h-4 w-4" /></button>
            </div>
            <p className="text-xs text-muted-foreground">{processFor.requester} · {processFor.category || 'refund'}. Load the payment in FNB, then upload the proof and link the GL account.</p>
            <div>
              <label className="text-xs font-medium">Payment proof (FNB screenshot) *</label>
              <input ref={proofRef} type="file" accept="image/*,.pdf" aria-label="Payment proof (FNB screenshot)" className="mt-1 block text-sm" />
            </div>
            <div className="relative">
              <label className="text-xs font-medium">Link GL account (so it&apos;s easy to post)</label>
              {glSel ? (
                <div className="mt-1 flex items-center justify-between text-sm border rounded-md px-3 py-2 bg-muted/40">
                  <span>{glSel.code} — {glSel.name}</span>
                  <button className="text-xs text-blue-600" onClick={() => { setGlSel(null); setGlQuery('') }}>change</button>
                </div>
              ) : (
                <>
                  <div className="mt-1 flex gap-2">
                    <input value={glQuery} onChange={e => setGlQuery(e.target.value)} placeholder="Search expense account (code or name)…"
                           className="flex-1 px-3 py-2 text-sm border rounded-md bg-background" />
                    <Button size="sm" variant="outline" disabled={glSuggesting} onClick={suggestGl} title="Let AI suggest the expense line">
                      <Sparkles className="h-4 w-4 mr-1" />{glSuggesting ? '…' : 'Suggest'}
                    </Button>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">Expense lines only — no balance-sheet, revenue, or reinsurance.</p>
                  {glOpts.length > 0 && (
                    <div className="absolute z-10 w-full mt-1 max-h-48 overflow-auto border rounded-md bg-background shadow">
                      {glOpts.map(a => (
                        <button key={a.id} onClick={() => { setGlSel(a); setGlOpts([]) }}
                                className="block w-full text-left px-3 py-1.5 text-sm hover:bg-muted">{a.code} — {a.name}</button>
                      ))}
                    </div>)}
                </>)}
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button size="sm" variant="outline" onClick={() => setProcessFor(null)}>Cancel</Button>
              <Button size="sm" disabled={busy} onClick={doProcess}><Upload className="h-4 w-4 mr-1" />Send to CFO</Button>
            </div>
          </CardContent></Card>
        </div>)}
    </div>
  )
}
