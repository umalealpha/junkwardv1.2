'use client'

/**
 * /intel-summary — Intelligence Summary (CFO 2026-07-26).
 *
 * One page that answers "how big is the book and what did we write" and, in the
 * same view, shows what Alpha Brain currently believes — so the 62,347 vs
 * 139,514 argument is settled on screen instead of in email.
 *
 * The headline policy number is OUR active book. Alpha Brain's figure appears
 * once, in the reconciliation strip, compared on its own basis and clearly
 * labelled. Counts and totals only — no customer appears on this page.
 *
 * A month with no posted revenue is labelled "not posted", never shown as zero:
 * ADIC's May/June 2026 are coming in as opening balances at 1 July 2026, and a
 * zero would read as "we earned nothing".
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { intelStaffSummary, type IntelSummary } from '@/lib/api'
import { Loader2, Copy, Check, RefreshCw, AlertTriangle } from 'lucide-react'

/** Pula, thousands-separated, no cents — board reading, not a ledger. */
function pula(v: string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return 'P' + n.toLocaleString('en-BW', { maximumFractionDigits: 0 })
}
const count = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : n.toLocaleString('en-BW')

/** Last 18 complete months, newest first. */
function monthOptions(): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = []
  const now = new Date()
  const cur = new Date(now.getFullYear(), now.getMonth(), 1)
  for (let i = 1; i <= 18; i++) {
    const d = new Date(cur.getFullYear(), cur.getMonth() - i, 1)
    out.push({
      value: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
      label: d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }),
    })
  }
  return out
}

export default function IntelSummaryPage() {
  const { theme } = useTheme()
  const months = useMemo(monthOptions, [])
  const [month, setMonth] = useState(months[0].value)
  const [data, setData] = useState<IntelSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  // `seq` discards a slow reply that lands after the user has already moved to
  // another month — otherwise switching quickly can leave the page showing the
  // wrong month's figures under the right month's label.
  const [seq, setSeq] = useState(0)
  const load = useCallback(async (m: string, isCurrent: () => boolean) => {
    setLoading(true); setError('')
    try {
      const r = await intelStaffSummary(m)
      if (isCurrent()) setData(r)
    } catch (e) {
      if (isCurrent()) {
        setError(e instanceof Error ? e.message : 'Could not load the summary.')
        setData(null)
      }
    } finally { if (isCurrent()) setLoading(false) }
  }, [])

  useEffect(() => {
    let live = true
    load(month, () => live)
    return () => { live = false }
  }, [month, seq, load])

  const card = {
    background: theme.card,
    border: `1px solid ${theme.cardBdr}`,
    borderRadius: 14,
    padding: '18px 20px',
  } as const

  const light: Record<string, { dot: string; bg: string; fg: string; label: string }> = {
    green: { dot: theme.ok, bg: theme.okB, fg: theme.ok, label: 'Agrees' },
    amber: { dot: theme.wr, bg: theme.wrB, fg: theme.wr, label: 'Drifting' },
    red:   { dot: theme.er, bg: theme.erB, fg: theme.er, label: 'No data' },
  }

  // "Not posted" must mean the ledger genuinely has nothing for this month —
  // NOT that the ledger could not be read. A GL outage is its own message.
  const glDown = !!data && data.premium === null
  const posted = !glDown && !!data?.month_posted
  const monthLabel = months.find(m => m.value === month)?.label ?? month
  // The opening-balance story is specific to ADIC's May/June/July 2026. Any
  // other entity or month gets the generic line instead of a wrong explanation.
  const openingBalanceMonth =
    data?.company === 'ADIC' && ['2026-05', '2026-06', '2026-07'].includes(month)

  /** The board-ready text behind the copy button. */
  const boardText = useMemo(() => {
    if (!data) return ''
    const p = data.policies, g = data.premium
    return [
      `Alpha Direct — ${data.company} — ${monthLabel}`,
      '',
      `Active policies: ${count(p?.active_total)}`,
      `  MIS ${count(p?.active_by_category?.MIS)} · DOM ${count(p?.active_by_category?.DOM)}` +
      ` · COM ${count(p?.active_by_category?.COM)}`,
      '',
      posted
        ? `GWP (${monthLabel}): ${pula(g?.month.gwp)}`
        : `GWP (${monthLabel}): not posted to the ledger`,
      `GWP (year to date from ${g?.financial_year_to_date.from ?? '—'}): ` +
      `${pula(g?.financial_year_to_date.gwp)}`,
      `PAT (year to date): ${pula(g?.financial_year_to_date.pat)}`,
      '',
      'Source: Omni general ledger (posted entries) + Graphite policy register.',
    ].join('\n')
  }, [data, monthLabel, posted])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(boardText)
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    } catch { setError('Could not copy — select the text and copy it manually.') }
  }

  return (
    <>
      <TopBar title="Intelligence Summary" />
      <div style={{ padding: '20px 24px 48px', maxWidth: 1180, margin: '0 auto' }}>

        {/* Page hero — rebuilt Omni screens (2026-09-02). */}
        <div style={{ marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 10.5, fontWeight: 650, letterSpacing: '0.11em', textTransform: 'uppercase', color: theme.orangeText }}>
            <span style={{ width: 5, height: 5, borderRadius: 99, background: theme.orange }} />
            Overview
          </div>
          <h1 style={{ margin: '6px 0 0', fontSize: 25, lineHeight: 1.15, letterSpacing: '-0.03em', fontWeight: 680, color: theme.text }}>Intelligence Summary</h1>
          <p style={{ margin: '6px 0 0', fontSize: 14, color: theme.t2 }}>One read on the book, the ledger and where the two disagree.</p>
        </div>

        {/* One date control for the whole page */}
        <div style={{ ...card, display: 'flex', flexWrap: 'wrap', gap: 14,
                      alignItems: 'center', marginBottom: 18 }}>
          <label htmlFor="intel-month" style={{ fontSize: 13, color: theme.t2 }}>Month</label>
          <select
            id="intel-month" value={month} onChange={e => setMonth(e.target.value)}
            style={{ padding: '8px 12px', borderRadius: 8, fontSize: 14,
                     border: `1px solid ${theme.b1}`, background: theme.input, color: theme.text }}
          >
            {months.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <span style={{ fontSize: 13, color: theme.t3 }}>
            Everything on this page moves with this month.
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            <button onClick={() => setSeq(n => n + 1)} aria-label="Refresh"
              style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px',
                       borderRadius: 8, fontSize: 13, cursor: 'pointer',
                       border: `1px solid ${theme.b1}`, background: 'transparent',
                       color: theme.text }}>
              <RefreshCw size={14} /> Refresh
            </button>
            <button onClick={copy} disabled={!data}
              style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px',
                       borderRadius: 8, fontSize: 13, fontWeight: 600, border: 'none',
                       cursor: data ? 'pointer' : 'not-allowed', color: '#FFFFFF',
                       background: data ? theme.orangeText : theme.g200 }}>
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy for the board'}
            </button>
          </div>
        </div>

        {error && (
          <div style={{ ...card, marginBottom: 18, background: theme.erB,
                        border: `1px solid ${theme.er}`, color: theme.er, fontSize: 14 }}>
            {error}
          </div>
        )}

        {loading && (
          <div style={{ ...card, display: 'flex', gap: 10, alignItems: 'center', color: theme.t2 }}>
            <Loader2 size={16} className="animate-spin" />
            Reading the ledger and the policy register…
          </div>
        )}

        {!loading && data && (
          <>
            {/* The book */}
            <div style={{ ...card, marginBottom: 18 }}>
              <div style={{ fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase',
                            color: theme.t2, marginBottom: 6 }}>
                The book — active policies
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 46, fontWeight: 700, color: theme.navy, lineHeight: 1.05 }}>
                  {count(data.policies?.active_total)}
                </div>
                <div style={{ fontSize: 13, color: theme.t3 }}>
                  activated and in force · Graphite policy register
                </div>
              </div>
              <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', marginTop: 14 }}>
                {(['MIS', 'DOM', 'COM'] as const).map(k => (
                  <div key={k}>
                    <div style={{ fontSize: 12, color: theme.t3 }}>{k}</div>
                    <div style={{ fontSize: 20, fontWeight: 600, color: theme.text }}>
                      {count(data.policies?.active_by_category?.[k])}
                    </div>
                  </div>
                ))}
              </div>
              {data.errors?.policies && (
                <div style={{ marginTop: 12, fontSize: 13, color: theme.er }}>
                  Policy register unavailable ({data.errors.policies}).
                </div>
              )}
            </div>

            {/* What we wrote */}
            <div style={{ display: 'grid', gap: 14, marginBottom: 18,
                          gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))' }}>
              <Tile theme={theme} label={`GWP — ${monthLabel}`}
                    value={glDown ? 'Unavailable' : posted ? pula(data.premium?.month.gwp) : 'Not posted'}
                    muted={!posted}
                    note={glDown ? 'the ledger could not be read'
                                 : posted ? 'posted journal entries'
                                 : 'no revenue posted to the ledger for this month'} />
              <Tile theme={theme} label="GWP — year to date"
                    value={pula(data.premium?.financial_year_to_date.gwp)}
                    note={`financial year from ${data.premium?.financial_year_to_date.from ?? '—'}`} />
              <Tile theme={theme} label="Profit after tax — year to date"
                    value={pula(data.premium?.financial_year_to_date.pat)}
                    note="posted journal entries" />
              <Tile theme={theme} label={`Net earned premium — ${monthLabel}`}
                    value={glDown ? 'Unavailable'
                                  : posted ? pula(data.premium?.month.net_earned_premium)
                                  : 'Not posted'}
                    muted={!posted}
                    note={posted ? 'after reinsurance and unearned movement' : '—'} />
            </div>

            {glDown && (
              <div style={{ ...card, marginBottom: 18, display: 'flex', gap: 10,
                            alignItems: 'flex-start', background: theme.erB,
                            border: `1px solid ${theme.er}` }}>
                <AlertTriangle size={18} style={{ color: theme.er, flexShrink: 0, marginTop: 2 }} />
                <div style={{ fontSize: 13.5, color: theme.text }}>
                  <strong>The ledger could not be read.</strong> These figures are missing because
                  the source failed, not because the month is empty
                  {data.errors?.premium ? ` (${data.errors.premium})` : ''}. The policy counts above
                  come from a different source and are unaffected.
                </div>
              </div>
            )}

            {!glDown && !posted && (
              <div style={{ ...card, marginBottom: 18, display: 'flex', gap: 10,
                            alignItems: 'flex-start', background: theme.wrB,
                            border: `1px solid ${theme.wr}` }}>
                <AlertTriangle size={18} style={{ color: theme.wr, flexShrink: 0, marginTop: 2 }} />
                <div style={{ fontSize: 13.5, color: theme.text }}>
                  <strong>{monthLabel} has no revenue posted to the ledger.</strong> It is shown as
                  “not posted” rather than zero so it is not read as a month with no business.
                  {openingBalanceMonth && ' ADIC’s May and June 2026 are being brought in as ' +
                    'opening balances at 1 July 2026.'}
                </div>
              </div>
            )}

            {/* Reconciliation strip — the argument, settled */}
            <div style={card}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <span style={{ width: 10, height: 10, borderRadius: 5, display: 'inline-block',
                               background: light[data.brain.agree].dot }} />
                <div style={{ fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase',
                              color: theme.t2 }}>
                  Omni vs Alpha Brain
                </div>
                <span style={{ fontSize: 12, fontWeight: 600, padding: '2px 10px', borderRadius: 20,
                               background: light[data.brain.agree].bg,
                               color: light[data.brain.agree].fg }}>
                  {light[data.brain.agree].label}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 34, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontSize: 12, color: theme.t3 }}>Omni, on the brain’s basis</div>
                  <div style={{ fontSize: 22, fontWeight: 600, color: theme.text }}>
                    {count(data.brain.our_same_basis_total)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 12, color: theme.t3 }}>Alpha Brain says</div>
                  <div style={{ fontSize: 22, fontWeight: 600, color: theme.text }}>
                    {count(data.brain.their_active_total)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 12, color: theme.t3 }}>Gap</div>
                  <div style={{ fontSize: 22, fontWeight: 600, color: theme.orangeText }}>
                    {data.brain.their_active_total !== null && data.brain.our_same_basis_total !== null
                      ? count(Math.abs(data.brain.their_active_total - data.brain.our_same_basis_total))
                      : '—'}
                  </div>
                </div>
              </div>
              <div style={{ marginTop: 12, fontSize: 13, color: theme.t2, maxWidth: 780 }}>
                {data.brain.basis_note}
              </div>
              {!data.brain.activated && (
                <div style={{ marginTop: 10, fontSize: 13, color: theme.wr }}>
                  Alpha Brain has not sent a figure yet
                  {data.brain.note ? ` — ${data.brain.note}` : '.'}
                </div>
              )}
              {data.brain.narrative && (
                <div style={{ marginTop: 12, fontSize: 13.5, fontStyle: 'italic', color: theme.t2 }}>
                  “{data.brain.narrative}”
                </div>
              )}
            </div>

            <div style={{ marginTop: 14, fontSize: 12, color: theme.t3 }}>
              {data.company} · read {new Date(data.generated_at).toLocaleString('en-GB')} ·
              counts and totals only, no customer information on this page.
            </div>
          </>
        )}
      </div>
    </>
  )
}

function Tile({ theme, label, value, note, muted = false }: {
  theme: ReturnType<typeof useTheme>['theme']
  label: string; value: string; note?: string; muted?: boolean
}) {
  return (
    <div style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`,
                  borderRadius: 14, padding: '16px 18px' }}>
      <div style={{ fontSize: 12, color: theme.t2, marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: muted ? 20 : 26, fontWeight: 700, lineHeight: 1.15,
                    color: muted ? theme.wr : theme.navy }}>
        {value}
      </div>
      {note && (
        <div style={{ fontSize: 12, color: theme.t3, marginTop: 6 }}>{note}</div>
      )}
    </div>
  )
}
