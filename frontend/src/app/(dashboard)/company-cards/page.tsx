'use client'

/**
 * /company-cards — the Finance side of company-card spending (CFO 2026-08-07).
 *
 * The MISSING-RECEIPT REPORT is the point of this screen, so it stays front
 * and centre: load a month's statement and Omni names the transactions nobody
 * has produced a receipt for. Without that, no one ever learns what was never
 * uploaded.
 *
 * The card register sits underneath, because the four cards were seeded with
 * 0000 — Omni never stores a card NUMBER, only the last four digits, and
 * somebody has to type those once.
 *
 * Cardholders upload on their phone (/m/staff/card-spend). Coding a spend to a
 * GL account is a separate queue, still to build.
 *
 * NOTE (2026-08-26 look-only redesign): the overview KPI tiles and the
 * per-card bar chart are PRESENTATION-ONLY roll-ups of the SAME register and
 * statement rows already fetched below — pure client-side counts, no new
 * endpoints, no new numbers. This page never loads individual spend amounts,
 * so no currency total is shown (we don't fabricate one).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch, apiFetchBinary } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  TableWrapper, Table, TableHead, TableHeader, TableBody, TableRow, TableCell, EmptyTableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/ui/modal'
import { Badge, StatusBadge } from '@/components/ui/badge'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import {
  AlertTriangle, CheckCircle2, CreditCard, Upload, Loader2, Pencil, Receipt, Layers,
  ArrowUpDown, Eye, MessageSquareText, BellRing, X, type LucideIcon,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = '"Book Antiqua", Palatino, "Palatino Linotype", Georgia, serif'
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface Card_ {
  id: string; label: string; last4: string; currency: string
  is_active: boolean; holder: string; spend_count: number
}
interface Stmt {
  id: string; card: string; holder: string; period: string
  total_lines: number; missing_count: number; uploaded_at: string
}
interface Gap { id: string; posted_on: string; description: string; amount: string }
interface UploadSummary {
  total_transactions: number; new_transactions: number
  duplicate_transactions: number
  duplicates: { posted_on: string; description: string; amount: string }[]
}
interface Spend {
  id: string; card_label: string; spent_on: string; merchant: string; amount: string
  currency: string; what_for: string; has_receipt: boolean; receipt_url: string | null
  is_explained: boolean; word_count: number; status: string; status_label: string
  uploaded_by: string; finance_note: string
}

// ─── Count-up hook (presentational — copied from the dashboard pack) ─────────
// Animates a number 0 → target once via requestAnimationFrame. Fed only the
// REAL value already computed for each KPI; never invents data. Snaps to the
// final value immediately under reduce-motion.
function useCountUp(target: number, durationMs = 900, enabled = true): number {
  const [val, setVal] = useState(enabled ? 0 : target)
  const rafRef = useRef<number | null>(null)
  useEffect(() => {
    if (!enabled || !isFinite(target) || target === 0) { setVal(target); return }
    let start: number | null = null
    const tick = (t: number) => {
      if (start === null) start = t
      const p = Math.min(1, (t - start) / durationMs)
      const eased = 1 - Math.pow(1 - p, 3)          // ease-out cubic
      setVal(target * eased)
      if (p < 1) rafRef.current = requestAnimationFrame(tick)
      else setVal(target)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [target, durationMs, enabled])
  return val
}

// ─── Glass KPI tile (local component, GlassKpiCard pattern) ──────────────────
interface KpiProps {
  label: string; raw: number; icon: LucideIcon; sub: string
  isOrange: boolean; index: number; theme: any; reduceMotion: boolean
}
function GlassKpiCard({ label, raw, icon: Icon, sub, isOrange, index, theme, reduceMotion }: KpiProps) {
  const animate = isFinite(raw) && !reduceMotion
  const counted = useCountUp(raw, 900, animate)
  const display = isFinite(raw) ? Math.round(counted).toLocaleString('en-US') : '—'
  const accent = isOrange ? ORANGE : theme.inf
  return (
    <div
      className="glass-card cc-rise relative overflow-hidden p-4 flex flex-col"
      style={{
        animationDelay: reduceMotion ? undefined : `${index * 50}ms`,
        animation: reduceMotion ? 'none' : undefined,
        minHeight: 112,
      }}
    >
      <span
        aria-hidden="true"
        className="absolute top-0 left-0 right-0 h-[2px]"
        style={{ background: `linear-gradient(90deg, ${accent}, transparent 80%)`, opacity: 0.7 }}
      />
      <div className="flex items-center gap-2.5">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: isOrange ? theme.oL : theme.inB }}
        >
          <Icon className="w-[18px] h-[18px]" style={{ color: accent }} strokeWidth={1.6} />
        </div>
        <span
          className="text-[11px] font-semibold uppercase tracking-[0.1em] leading-tight"
          style={{ color: theme.t2 }}
        >
          {label}
        </span>
      </div>
      <div
        className="font-display-tight font-bold mt-3 tabular-nums whitespace-nowrap"
        style={{ color: theme.navy, fontSize: 'clamp(20px, 2vw, 30px)' }}
      >
        {display}
      </div>
      <div className="text-xs mt-1" style={{ color: theme.t3 }}>{sub}</div>
    </div>
  )
}

type SortKey = 'label' | 'holder' | 'spend_count'

export default function CompanyCardsPage() {
  const { theme, reduceMotion } = useTheme()
  const [cards, setCards] = useState<Card_[]>([])
  const [stmts, setStmts] = useState<Stmt[]>([])
  const [open, setOpen] = useState<Stmt | null>(null)
  const [gaps, setGaps] = useState<Gap[]>([])
  const [spends, setSpends] = useState<Spend[]>([])
  const [preview, setPreview] = useState<{ url: string; pdf: boolean } | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  // statement upload
  const [upCard, setUpCard] = useState('')
  const [upYear, setUpYear] = useState(new Date().getFullYear())
  const [upMonth, setUpMonth] = useState(new Date().getMonth() + 1)
  const [upFile, setUpFile] = useState<File | null>(null)

  // statement housekeeping: move to the right month, or delete a mis-load
  // (Laone Thebe 2026-09-11). Deleting always asks first.
  const [toDelete, setToDelete] = useState<Stmt | null>(null)
  // Last upload's import summary — how many came in, how many were already
  // there. Shown so a skipped duplicate is visible, never silent.
  const [summary, setSummary] = useState<UploadSummary | null>(null)

  // register table sort (DISPLAY-ONLY — never mutates the fetched rows)
  const [sortKey, setSortKey] = useState<SortKey>('label')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [reg, st, sp] = await Promise.all([
        apiFetch<{ cards: Card_[] }>('/company-cards/register/'),
        apiFetch<{ statements: Stmt[] }>('/company-cards/statements/'),
        apiFetch<{ spends: Spend[] }>('/company-cards/spends/'),
      ])
      setCards(reg.cards || [])
      setStmts(st.statements || [])
      setSpends(sp.spends || [])
      if (!upCard && reg.cards?.length) setUpCard(reg.cards[0].id)
    } catch {
      setDenied(true)
    } finally {
      setLoading(false)
    }
  }, [upCard])
  useEffect(() => { load() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const openStatement = async (s: Stmt) => {
    setOpen(s); setGaps([])
    try {
      const d = await apiFetch<{ missing: Gap[] }>(`/company-cards/statements/${s.id}/gaps/`)
      setGaps(d.missing || [])
    } catch {
      setMsg({ ok: false, text: 'Could not load that statement.' })
    }
  }

  const upload = async (force = false) => {
    if (!upCard || !upFile || busy) return
    setBusy(true); setMsg(null); setSummary(null)
    try {
      const fd = new FormData()
      fd.append('card', upCard); fd.append('year', String(upYear))
      fd.append('month', String(upMonth)); fd.append('file', upFile)
      if (force) fd.append('force', '1')
      const r = await apiFetch<{ message: string } & UploadSummary>(
        '/company-cards/statements/upload/', { method: 'POST', body: fd })
      setMsg({ ok: true, text: r.message })
      setSummary({
        total_transactions: r.total_transactions,
        new_transactions: r.new_transactions,
        duplicate_transactions: r.duplicate_transactions,
        duplicates: r.duplicates || [],
      })
      setUpFile(null)
      await load()
    } catch (e) {
      // The server refuses an identical file it has already read (409) rather
      // than loading it twice. Offer the override instead of a dead end.
      const text = e instanceof Error ? e.message : 'Upload failed.'
      if (/already been loaded/i.test(text) &&
          window.confirm(`${text}\n\nLoad it anyway?`)) {
        setBusy(false)
        return upload(true)
      }
      setMsg({ ok: false, text })
    } finally {
      setBusy(false)
    }
  }

  // Move a statement to the month it actually belongs to. Nothing is
  // re-imported, so this can never duplicate a transaction.
  const reallocate = async (s: Stmt) => {
    const [y0, m0] = s.period.split('-')
    const entered = window.prompt(
      `Move "${s.card} ${s.period}" to which month? Type it as YYYY-MM.`,
      `${y0}-${m0}`)
    if (!entered) return
    const m = entered.trim().match(/^(\d{4})-(\d{1,2})$/)
    if (!m) {
      setMsg({ ok: false, text: 'Type the month as YYYY-MM, e.g. 2026-08.' })
      return
    }
    setBusy(true); setMsg(null)
    try {
      const r = await apiFetch<{ message: string }>(
        `/company-cards/statements/${s.id}/reallocate/`,
        { method: 'POST', body: JSON.stringify({ year: Number(m[1]), month: Number(m[2]) }) })
      setMsg({ ok: true, text: r.message })
      if (open?.id === s.id) setOpen(null)
      await load()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not move it.' })
    } finally {
      setBusy(false)
    }
  }

  // Delete a mis-loaded statement. Receipts are NEVER deleted with it.
  const doDelete = async () => {
    const s = toDelete
    if (!s || busy) return
    setBusy(true); setMsg(null)
    try {
      const r = await apiFetch<{ message: string }>(
        `/company-cards/statements/${s.id}/delete/`, { method: 'DELETE' })
      setMsg({ ok: true, text: r.message })
      setToDelete(null)
      if (open?.id === s.id) { setOpen(null); setGaps([]) }
      await load()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not delete it.' })
    } finally {
      setBusy(false)
    }
  }

  const setLast4 = async (card: Card_) => {
    const entered = window.prompt(
      `Last FOUR digits of "${card.label}" (Omni never stores the full number):`,
      card.last4 === '0000' ? '' : card.last4)
    if (entered === null) return
    try {
      const d = await apiFetch<{ cards: Card_[] }>('/company-cards/register/', {
        method: 'PATCH',
        body: JSON.stringify({ id: card.id, last4: entered }),
      })
      setCards(d.cards || [])
      setMsg({ ok: true, text: `${card.label} updated.` })
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not save.' })
    }
  }

  const waive = async (g: Gap) => {
    const why = window.prompt(`Why does "${g.description}" need no receipt?`)
    if (!why?.trim()) return
    try {
      await apiFetch(`/company-cards/statement-lines/${g.id}/waive/`, {
        method: 'POST', body: JSON.stringify({ note: why.trim() }),
      })
      setGaps(gs => gs.filter(x => x.id !== g.id))
      await load()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not save.' })
    }
  }

  // Preview a receipt (auth'd binary → object URL). Never world-readable, so we
  // fetch with the user's token rather than a plain <img src>.
  const openReceipt = async (s: Spend) => {
    if (!s.receipt_url) return
    try {
      const res = await apiFetchBinary(s.receipt_url)
      const blob = await res.blob()
      setPreview({ url: URL.createObjectURL(blob), pdf: blob.type.includes('pdf') })
    } catch {
      setMsg({ ok: false, text: 'Could not open that receipt.' })
    }
  }
  const closePreview = () => { if (preview) URL.revokeObjectURL(preview.url); setPreview(null) }

  // Ask the cardholder about a charge — flips it to "queried", stores the
  // question and pushes it to their phone + morning brief.
  const askAbout = async (s: Spend) => {
    const note = window.prompt(
      `Ask ${s.uploaded_by} about this ${s.currency} ${s.amount} charge — what do you need to know?`)
    if (!note?.trim()) return
    try {
      await apiFetch(`/company-cards/spends/${s.id}/code/`, {
        method: 'POST', body: JSON.stringify({ queried: true, note: note.trim() }),
      })
      setMsg({ ok: true, text: `Asked ${s.uploaded_by}. It is on their phone and in their morning brief.` })
      await load()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not send the question.' })
    }
  }

  // One bundled nudge to every cardholder with something outstanding — replaces
  // the monthly chase email.
  const nudgeEveryone = async () => {
    if (busy) return
    setBusy(true); setMsg(null)
    try {
      const r = await apiFetch<{ message: string }>('/company-cards/nudge/', { method: 'POST' })
      setMsg({ ok: true, text: r.message })
      await load()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Could not nudge.' })
    } finally {
      setBusy(false)
    }
  }

  // ── DERIVED FOR DISPLAY ONLY — client-side roll-ups of already-fetched rows.
  //    No fetch, no new number: each value is a plain count/sum of loaded data.
  const needFixing = cards.filter(c => c.last4 === '0000')
  const totalCards = cards.length
  const missingLast4 = needFixing.length
  const noReceipt = stmts.reduce((a, s) => a + s.missing_count, 0)
  const txTracked = stmts.reduce((a, s) => a + s.total_lines, 0)
  const queriedN = spends.filter(s => s.status === 'queried').length
  const needWordsN = spends.filter(s => s.status !== 'coded' && !s.is_explained).length

  // Per-card roll-up for the bar chart: group the loaded statements by card and
  // sum their transaction / missing-receipt counts. (Spend amounts are not
  // fetched on this screen, so the chart shows counts, not currency.)
  const perCard = useMemo(() => {
    const m = new Map<string, { card: string; transactions: number; missing: number }>()
    for (const s of stmts) {
      const cur = m.get(s.card) || { card: s.card, transactions: 0, missing: 0 }
      cur.transactions += s.total_lines
      cur.missing += s.missing_count
      m.set(s.card, cur)
    }
    return Array.from(m.values())
  }, [stmts])

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(k); setSortDir('asc') }
  }
  const sortedCards = useMemo(() => {
    const arr = [...cards]
    arr.sort((a, b) => {
      const cmp = sortKey === 'spend_count'
        ? a.spend_count - b.spend_count
        : String(a[sortKey]).localeCompare(String(b[sortKey]))
      return sortDir === 'asc' ? cmp : -cmp
    })
    return arr
  }, [cards, sortKey, sortDir])

  if (denied) {
    return (
      <div className="min-h-screen" style={{ background: theme.bg }}>
        <TopBar />
        <main className="max-w-3xl mx-auto px-6 py-16">
          <Card className="p-10 text-center">
            <CreditCard className="w-8 h-8 mx-auto mb-3" style={{ color: theme.t2 }} />
            <h1 className="text-lg font-semibold" style={{ color: theme.text, fontFamily: SERIF }}>Finance only</h1>
            <p className="text-sm mt-1" style={{ color: theme.t2 }}>
              Company-card statements are visible to the finance team.
            </p>
          </Card>
        </main>
      </div>
    )
  }

  const SortHeader = ({ label, k, align = 'left' }: { label: string; k: SortKey; align?: 'left' | 'right' }) => {
    const active = sortKey === k
    return (
      <TableHeader className={align === 'right' ? 'text-right' : ''}>
        <button
          type="button"
          onClick={() => toggleSort(k)}
          className={`inline-flex items-center gap-1 uppercase tracking-wider ${align === 'right' ? 'flex-row-reverse' : ''}`}
          style={{ color: active ? NAVY : undefined }}
          aria-label={`Sort by ${label} ${active ? (sortDir === 'asc' ? 'descending' : 'ascending') : 'ascending'}`}
        >
          {label}
          <ArrowUpDown className="w-3 h-3" style={{ opacity: active ? 1 : 0.4, color: active ? ORANGE : 'currentColor' }} />
        </button>
      </TableHeader>
    )
  }

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar />
      <main className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        <style>{`
          @keyframes cc-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
          .cc-rise { animation: cc-rise 360ms var(--ease-out, ease-out) both; }
        `}</style>

        {/* ── page header ──────────────────────────────────────────────── */}
        <header>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: theme.navy, fontFamily: SERIF }}>
            Company cards
          </h1>
          <p className="text-sm mt-1" style={{ color: theme.t2 }}>
            Load a month&rsquo;s statement and Omni names every transaction with no receipt.
          </p>
        </header>

        {/* ── overview tiles (derived from the register + statements below) ─ */}
        <section aria-label="Overview" className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
          <GlassKpiCard index={0} theme={theme} reduceMotion={reduceMotion}
            label="Company cards" raw={totalCards} icon={CreditCard} isOrange={false}
            sub="in the register" />
          <GlassKpiCard index={1} theme={theme} reduceMotion={reduceMotion}
            label="Missing last-four" raw={missingLast4} icon={AlertTriangle} isOrange={missingLast4 > 0}
            sub={missingLast4 > 0 ? 'set the digits' : 'all captured'} />
          <GlassKpiCard index={2} theme={theme} reduceMotion={reduceMotion}
            label="No receipt" raw={noReceipt} icon={Receipt} isOrange={noReceipt > 0}
            sub={noReceipt > 0 ? 'lines to chase' : 'all accounted for'} />
          <GlassKpiCard index={3} theme={theme} reduceMotion={reduceMotion}
            label="Transactions tracked" raw={txTracked} icon={Layers} isOrange={false}
            sub="across loaded statements" />
        </section>

        {msg && (
          <div className="rounded-xl px-4 py-3 text-sm"
               style={{ background: msg.ok ? '#ECFDF5' : '#FEF2F2',
                        color: msg.ok ? '#065F46' : '#B42318',
                        border: `1px solid ${msg.ok ? '#A7F3D0' : '#FECACA'}` }}>
            {msg.text}
          </div>
        )}

        {needFixing.length > 0 && (
          <div className="rounded-xl px-4 py-3 text-sm flex items-start gap-2"
               style={{ background: '#FFFBEB', border: '1px solid #FDE68A', color: '#92400E' }}>
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>
              <b>{needFixing.length} card(s) still show ••••0000.</b> Omni never stores a card
              number — set the last four digits in the register below so a statement can be
              matched to the right card.
            </span>
          </div>
        )}

        {/* ── load a statement ─────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Upload className="w-4 h-4" style={{ color: ORANGE }} />
              <CardTitle>Load a statement</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3 items-center">
              <select value={upCard} onChange={e => setUpCard(e.target.value)}
                      aria-label="Card"
                      className="rounded-lg px-3 py-2 text-sm"
                      style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                {cards.map(c => <option key={c.id} value={c.id}>{c.label} ••••{c.last4}</option>)}
              </select>
              <select value={upMonth} onChange={e => setUpMonth(Number(e.target.value))}
                      aria-label="Month"
                      className="rounded-lg px-3 py-2 text-sm"
                      style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
              <input type="number" value={upYear} onChange={e => setUpYear(Number(e.target.value))}
                     aria-label="Year"
                     className="rounded-lg px-3 py-2 text-sm w-24"
                     style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }} />
              <input type="file" accept=".csv,.xlsx,.xls" aria-label="Statement file"
                     onChange={e => setUpFile(e.target.files?.[0] || null)}
                     className="text-sm" style={{ color: theme.t2 }} />
              <button onClick={() => upload()} disabled={!upFile || busy}
                      className="rounded-lg px-4 py-2 text-sm font-semibold text-white transition-opacity disabled:opacity-50"
                      style={{ background: NAVY }}>
                {busy ? 'Reading…' : 'Load and match'}
              </button>
            </div>
            <p className="text-xs mt-3" style={{ color: theme.t2 }}>
              CSV or Excel, straight from the bank — Omni reads the column headings itself.
              Refunds are ignored; only spending needs a receipt.
            </p>
          </CardContent>
        </Card>

        {/* ── the missing-receipt report ───────────────────────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Receipt className="w-4 h-4" style={{ color: ORANGE }} />
              <CardTitle>Statements</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            {loading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t2 }} />}
            {!loading && stmts.length === 0 && (
              <p className="text-sm" style={{ color: theme.t2 }}>
                No statements loaded yet. Load one above to see what has no receipt.
              </p>
            )}
            <div className="space-y-2">
              {stmts.map(s => (
                <div key={s.id}
                     className="w-full flex items-center gap-3 rounded-xl px-4 py-3 text-left transition-colors"
                     style={{ background: open?.id === s.id ? theme.oL : theme.g100,
                              border: `1px solid ${open?.id === s.id ? ORANGE : 'transparent'}` }}>
                  <button onClick={() => openStatement(s)}
                          className="flex-1 text-left hover:brightness-95">
                    <div className="font-semibold text-sm" style={{ color: theme.text }}>
                      {s.card} · {s.period}
                    </div>
                    <div className="text-xs" style={{ color: theme.t2 }}>
                      {s.holder} · {s.total_lines} transaction(s)
                      {s.uploaded_at && <> · loaded {formatDate(s.uploaded_at)}</>}
                    </div>
                  </button>
                  {s.missing_count === 0
                    ? <Badge variant="success">
                        <CheckCircle2 className="w-3.5 h-3.5 mr-1" />All accounted for
                      </Badge>
                    : <Badge variant="danger">{s.missing_count} with no receipt</Badge>}
                  {/* Filed under the wrong month, or loaded in error — fix it
                      here instead of living with it (Laone Thebe 2026-09-11). */}
                  <button onClick={() => reallocate(s)} disabled={busy}
                          title="Move this statement to another month"
                          className="text-xs font-semibold underline whitespace-nowrap disabled:opacity-50"
                          style={{ color: NAVY }}>
                    Move month
                  </button>
                  <button onClick={() => setToDelete(s)} disabled={busy}
                          title="Delete this statement"
                          className="text-xs font-semibold underline whitespace-nowrap disabled:opacity-50"
                          style={{ color: '#B91C1C' }}>
                    Delete
                  </button>
                </div>
              ))}
            </div>

            {summary && (
              <div className="mt-4 rounded-xl p-4"
                   style={{ background: theme.g100, border: `1px solid ${theme.inB}` }}>
                <b className="text-sm" style={{ color: theme.text }}>Statement upload complete</b>
                <div className="text-sm mt-1" style={{ color: theme.t2 }}>
                  Total transactions: {summary.total_transactions} ·
                  {' '}New: {summary.new_transactions} ·
                  {' '}Already loaded (skipped): {summary.duplicate_transactions}
                </div>
                {summary.duplicates.length > 0 && (
                  <ul className="mt-2 text-xs space-y-1" style={{ color: theme.t2 }}>
                    {summary.duplicates.map((d, i) => (
                      <li key={i}>
                        {d.posted_on} · {d.description || '—'} · {d.amount}
                        {' '}— already in Omni, not loaded again
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {open && (
              <div className="mt-4 rounded-xl p-4" style={{ background: '#FFFBEB', border: '1px solid #FDE68A' }}>
                <div className="flex items-center gap-2 mb-2">
                  <AlertTriangle className="w-4 h-4" style={{ color: '#92400E' }} />
                  <b className="text-sm" style={{ color: '#92400E' }}>
                    No receipt — {open.card} {open.period}
                  </b>
                </div>
                {gaps.length === 0
                  ? <p className="text-sm" style={{ color: '#92400E' }}>
                      Every transaction on this statement has a receipt. ✅
                    </p>
                  : (
                    <table className="w-full text-sm">
                      <tbody>
                        {gaps.map(g => (
                          <tr key={g.id} style={{ borderTop: '1px solid #FDE68A' }}>
                            <td className="py-2" style={{ color: '#92400E' }}>{g.posted_on}</td>
                            <td className="py-2" style={{ color: '#92400E' }}>{g.description || '—'}</td>
                            <td className="py-2 text-right font-semibold tabular-nums" style={{ color: '#92400E' }}>
                              {g.amount}
                            </td>
                            <td className="py-2 text-right">
                              <button onClick={() => waive(g)}
                                      className="text-xs font-semibold underline" style={{ color: '#B04E00' }}>
                                No receipt needed
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
              </div>
            )}
          </CardContent>
        </Card>

        <ConfirmDialog
          open={toDelete !== null}
          onOpenChange={o => { if (!o) setToDelete(null) }}
          title="Delete bank statement?"
          description={toDelete
            ? `Are you sure you want to delete the ${toDelete.card} statement for `
              + `${toDelete.period}? This action cannot be undone. Its `
              + `${toDelete.total_lines} transaction line(s) go with it. Receipts `
              + `already uploaded by cardholders are NOT deleted.`
            : ''}
          confirmLabel="Delete"
          cancelLabel="Cancel"
          variant="danger"
          loading={busy}
          onConfirm={doDelete}
        />

        {/* ── card spends: preview the bill, ask the cardholder ────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <Receipt className="w-4 h-4" style={{ color: ORANGE }} />
                <CardTitle>Card spends</CardTitle>
                {queriedN > 0 && <Badge variant="warning">{queriedN} queried</Badge>}
                {needWordsN > 0 && <Badge variant="danger">{needWordsN} need a reason</Badge>}
              </div>
              <button onClick={nudgeEveryone} disabled={busy}
                      className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold text-white transition-opacity disabled:opacity-50"
                      style={{ background: NAVY }}>
                <BellRing className="w-4 h-4" />Nudge everyone
              </button>
            </div>
          </CardHeader>
          <CardContent className="px-0 py-0">
            <TableWrapper>
              <Table>
                <TableHead>
                  <tr>
                    <TableHeader>Date</TableHeader>
                    <TableHeader>Charge</TableHeader>
                    <TableHeader className="text-right">Amount</TableHeader>
                    <TableHeader>Holder</TableHeader>
                    <TableHeader>Explanation</TableHeader>
                    <TableHeader className="text-right">Action</TableHeader>
                  </tr>
                </TableHead>
                <TableBody>
                  {spends.length === 0
                    ? <EmptyTableRow colSpan={6} message="No card spends uploaded yet." />
                    : spends.map(s => (
                      <TableRow key={s.id} highlight={s.status === 'queried' ? 'warning' : undefined}>
                        <TableCell style={{ color: theme.t2 }}>{s.spent_on}</TableCell>
                        <TableCell style={{ color: theme.text }}>{s.merchant || '—'}</TableCell>
                        <TableCell className="text-right tabular-nums" style={{ color: theme.text }}>
                          {s.currency} {s.amount}
                        </TableCell>
                        <TableCell style={{ color: theme.t2 }}>{s.uploaded_by}</TableCell>
                        <TableCell>
                          <div className="max-w-xs">
                            {s.status === 'coded'
                              ? <Badge variant="success">Coded</Badge>
                              : s.status === 'queried'
                                ? <Badge variant="warning">Finance asked</Badge>
                                : s.is_explained
                                  ? <Badge variant="success">Explained</Badge>
                                  : <Badge variant="danger">Needs 25 words</Badge>}
                            {s.what_for && (
                              <p className="text-xs mt-1 truncate" style={{ color: theme.t2 }} title={s.what_for}>
                                {s.what_for}
                              </p>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-right whitespace-nowrap">
                          {s.has_receipt && (
                            <button onClick={() => openReceipt(s)}
                                    className="inline-flex items-center gap-1 text-xs font-semibold mr-3"
                                    style={{ color: NAVY }}>
                              <Eye className="w-3.5 h-3.5" />Preview
                            </button>
                          )}
                          {s.status !== 'coded' && (
                            <button onClick={() => askAbout(s)}
                                    className="inline-flex items-center gap-1 text-xs font-semibold"
                                    style={{ color: '#B04E00' }}>
                              <MessageSquareText className="w-3.5 h-3.5" />Ask
                            </button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            </TableWrapper>
            <p className="text-xs px-5 py-3" style={{ color: theme.t2 }}>
              &ldquo;Ask&rdquo; sends the question to the cardholder&rsquo;s phone and morning brief.
              They reply with the receipt and a short reason — no more chase emails.
            </p>
          </CardContent>
        </Card>

        {/* ── per-card roll-up chart (derived from the statements above) ──── */}
        <Card>
          <CardHeader>
            <CardTitle>Transactions by card</CardTitle>
          </CardHeader>
          <CardContent>
            {perCard.length === 0
              ? <p className="text-sm py-8 text-center" style={{ color: theme.t2 }}>
                  Load a statement to see transactions per card.
                </p>
              : (
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={perCard} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#EEF1F5" vertical={false} />
                    <XAxis dataKey="card" tick={{ fontSize: 12, fill: '#6B7280' }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <Tooltip
                      cursor={{ fill: 'rgba(13,27,42,0.04)' }}
                      formatter={(v: any) => Number(v).toLocaleString()}
                      contentStyle={{ borderRadius: 10, border: `1px solid ${theme.cardBdr}`, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="transactions" name="Transactions" fill={NAVY} radius={[4, 4, 0, 0]} maxBarSize={56} />
                    <Bar dataKey="missing" name="No receipt" fill={ORANGE} radius={[4, 4, 0, 0]} maxBarSize={56} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            <p className="text-xs mt-2" style={{ color: theme.t3 }}>
              Counts rolled up from loaded statements — orange bars are transactions still missing a receipt.
            </p>
          </CardContent>
        </Card>

        {/* ── the card register ────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CreditCard className="w-4 h-4" style={{ color: ORANGE }} />
              <CardTitle>Card register</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="px-0 py-0">
            <TableWrapper>
              <Table>
                <TableHead>
                  <tr>
                    <SortHeader label="Card" k="label" />
                    <SortHeader label="Holder" k="holder" />
                    <SortHeader label="Spends" k="spend_count" align="right" />
                    <TableHeader>Status</TableHeader>
                    <TableHeader className="text-right">Action</TableHeader>
                  </tr>
                </TableHead>
                <TableBody>
                  {sortedCards.length === 0
                    ? <EmptyTableRow colSpan={5} message="No cards in the register yet." />
                    : sortedCards.map(c => {
                      const needs = c.last4 === '0000'
                      return (
                        <TableRow key={c.id} highlight={needs ? 'warning' : undefined}>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <span className="font-semibold" style={{ color: theme.text }}>{c.label}</span>
                              <span className="tabular-nums" style={{ color: theme.t2 }}>••••{c.last4}</span>
                              {needs && <Badge variant="warning">set digits</Badge>}
                            </div>
                          </TableCell>
                          <TableCell style={{ color: theme.t2 }}>{c.holder}</TableCell>
                          <TableCell className="text-right tabular-nums" style={{ color: theme.text }}>
                            {c.spend_count}
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={c.is_active ? 'active' : 'inactive'} />
                          </TableCell>
                          <TableCell className="text-right">
                            <button onClick={() => setLast4(c)}
                                    className="inline-flex items-center gap-1 text-xs font-semibold"
                                    style={{ color: '#B04E00' }}>
                              <Pencil className="w-3.5 h-3.5" />Set last four
                            </button>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                </TableBody>
              </Table>
            </TableWrapper>
            <p className="text-xs px-5 py-3" style={{ color: theme.t2 }}>
              Omni stores the last four digits only — never a full card number.
            </p>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
