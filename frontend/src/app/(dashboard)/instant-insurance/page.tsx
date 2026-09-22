'use client'

/**
 * /instant-insurance — the Instant Insurance book, and whether the money reaches us.
 *
 * Built to Build Brief 1 (Data department, 2 September 2026), Stage D: a panel per
 * book, broken down by product, with the exception box on the SAME screen rather
 * than a separate tab, and collections always labelled cash rather than premium.
 *
 * The reconciliation panel sits ABOVE the book, deliberately. On 8 September 2026 we
 * found that roughly 22,500 successful RealPay collections from July and August had
 * never reached the payment ledger, and nothing in Omni had noticed for two months.
 * A book size is worth very little if the collections behind it are going missing
 * quietly, so the break is the first thing on the page and not a footnote.
 *
 * There is not a single number in this markup. Every figure is read live on load.
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Loader2, RefreshCw, AlertTriangle, CheckCircle2, Landmark, Info,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getInstantInsuranceSummary, getRealpayLedgerReconciliation, getToken,
  type InstantSummary, type ReconResult,
} from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const DASH = '—'

const nf = (n: number | null | undefined) =>
  n === null || n === undefined ? DASH : Number(n).toLocaleString('en-BW')
const pf = (n: number | null | undefined, dp = 1) =>
  n === null || n === undefined ? DASH : `${Number(n).toFixed(dp)}%`
const mf = (n: number | null | undefined) =>
  n === null || n === undefined ? DASH
    : 'P' + Number(n).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dfmt = (iso?: string) => {
  if (!iso) return DASH
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? DASH
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' })
}
const fmtStamp = (iso?: string | null) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}
const monthName = (ym: string) => {
  const [y, m] = ym.split('-').map(Number)
  if (!y || !m) return ym
  return new Date(Date.UTC(y, m - 1, 1))
    .toLocaleDateString('en-GB', { month: 'short', year: '2-digit' })
}

export default function InstantInsurancePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [book, setBook] = useState<InstantSummary | null>(null)
  const [recon, setRecon] = useState<ReconResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [stamp, setStamp] = useState('')
  const [reconDenied, setReconDenied] = useState(false)
  const [reconFailed, setReconFailed] = useState(false)
  const [reconLoading, setReconLoading] = useState(false)

  // The two panels are fetched INDEPENDENTLY, not with Promise.all or even
  // allSettled awaited together. Two reasons, both learned the hard way:
  //   * the book is open to any signed-in user while the reconciliation is
  //     finance-only, so one refusal must not blank the panel the reader IS
  //     allowed to see (the dashboard learned this on 2026-07-24); and
  //   * the reconciliation scans a 6.6m-row feed and takes ~22s on prod, so
  //     waiting for it would hide the fast book behind a 22-second spinner.
  const load = useCallback((forceLive = false) => {
    setLoading(true); setErr(null); setReconDenied(false); setReconFailed(false)
    const now = () => new Date().toLocaleString('en-GB', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })

    getInstantInsuranceSummary()
      .then(b => { setBook(b); setStamp(now()) })
      .catch(() => setErr('Data unavailable — check the connection to the reporting copy'))
      .finally(() => setLoading(false))

    setReconLoading(true)
    getRealpayLedgerReconciliation(6, forceLive)
      .then(r => setRecon(r))
      .catch(e => {
        setRecon(null)
        // A refusal and an outage are different things and must read differently.
        // Matching only "permission" left a timeout or a 500 rendering NOTHING —
        // an alarm panel that quietly disappears is the failure this whole module
        // exists to prevent.
        if (/permission|denied|restricted|forbidden|403/i.test(String(e ?? ''))) {
          setReconDenied(true)
        } else {
          setReconFailed(true)
        }
      })
      .finally(() => setReconLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}`, borderRadius: 14 }
  const broken = recon?.available && recon.verdict === 'break'

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Instant Insurance" breadcrumbs={[{ label: 'Instant Insurance' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">

        <header className="flex flex-wrap items-end justify-between gap-3 pb-3"
                style={{ borderBottom: `3px solid ${ORANGE}` }}>
          <div>
            <h1 className="text-2xl font-bold" style={{ color: theme.text }}>Instant Insurance</h1>
            <p className="text-sm mt-0.5" style={{ color: theme.t2 }}>
              The book, and whether the collections behind it are reaching us
            </p>
          </div>
          <div className="text-right text-xs" style={{ color: theme.t2 }}>
            <div className="text-sm font-semibold" style={{ color: theme.text }}>
              As at {dfmt(book?.asof)}
            </div>
            <div className="mt-0.5">Reads live · not a frozen month-end snapshot</div>
            <div className="mt-1 flex items-center justify-end gap-2">
              <span>Last updated {stamp || DASH}</span>
              <button onClick={() => load(true)} disabled={loading} aria-label="Refresh"
                      className="inline-flex items-center gap-1.5 text-xs font-semibold rounded px-2 py-1"
                      style={{ border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
              </button>
            </div>
          </div>
        </header>

        {loading && !book && (
          <div className="text-sm" style={{ color: theme.t2 }}>
            <Loader2 className="w-4 h-4 inline animate-spin" /> Loading…
          </div>
        )}
        {err && (
          <div className="rounded-lg px-3 py-2 text-sm" role="alert"
               style={{ background: theme.erB, color: theme.er }}>{err}</div>
        )}

        {/* ── Does the money reach us? First, not a footnote. ─────────── */}
        {reconDenied && (
          <section className="p-4" style={card}>
            <h2 className="text-base font-bold" style={{ color: theme.text }}>
              Does what RealPay collects reach our payment records?
            </h2>
            <p className="text-sm mt-1" style={{ color: theme.t2 }}>
              This panel is restricted to finance and management staff. The book below is
              unaffected.
            </p>
          </section>
        )}
        {reconFailed && (
          <section className="p-4" style={card} role="alert">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" style={{ color: theme.wr }} />
              <div>
                <h2 className="text-base font-bold" style={{ color: theme.text }}>
                  Does what RealPay collects reach our payment records?
                </h2>
                <p className="text-sm mt-0.5" style={{ color: theme.wr }}>
                  Could not check. Treat this as an outage, not a clean pass — press
                  Refresh, and if it keeps failing the daily 06:00 check will say so too.
                </p>
              </div>
            </div>
          </section>
        )}
        {reconLoading && !recon && !reconDenied && !reconFailed && (
          <section className="p-4" style={card}>
            <p className="text-sm" style={{ color: theme.t2 }}>
              <Loader2 className="w-4 h-4 inline animate-spin" /> Checking RealPay against
              our payment records…
            </p>
          </section>
        )}
        {recon && (
          <section className="p-4" style={card} aria-labelledby="recon-h">
            <div className="flex items-start gap-2 mb-3">
              {broken ? <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" style={{ color: theme.wr }} />
                : <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" style={{ color: theme.ok }} />}
              <div>
                <h2 id="recon-h" className="text-base font-bold" style={{ color: theme.text }}>
                  Does what RealPay collects reach our payment records?
                </h2>
                <p className="text-sm mt-0.5" style={{ color: broken ? theme.wr : theme.t2 }}>
                  {recon.available ? recon.summary
                    : `Could not check — ${recon.reason}. Treat this as an outage, not a clean pass.`}
                </p>
                {recon.available && (
                  <p className="text-xs mt-1" style={{ color: theme.t3 }}>
                    {recon.served_from === 'the daily check'
                      ? `From this morning's check, run ${fmtStamp(recon.computed_at)}. Refresh
                         re-checks against the database now — it takes about twenty seconds.`
                      : 'Checked against the database just now.'}
                  </p>
                )}
              </div>
            </div>

            {recon.available && recon.rows && (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm tabular-nums">
                    <thead>
                      <tr style={{ background: NAVY, color: '#fff' }}>
                        <th className="text-left px-3 py-2 font-semibold">Month</th>
                        <th className="text-right px-3 py-2 font-semibold">RealPay collected</th>
                        <th className="text-right px-3 py-2 font-semibold">Reached our records</th>
                        <th className="text-right px-3 py-2 font-semibold">Missing</th>
                        <th className="text-right px-3 py-2 font-semibold">Worth roughly</th>
                        <th className="text-left px-3 py-2 font-semibold">State</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recon.rows.map((r, i) => (
                        <tr key={r.month} style={{ background: i % 2 ? theme.g50 : 'transparent' }}>
                          <td className="px-3 py-2" style={{ color: theme.text }}>{monthName(r.month)}</td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{nf(r.feed)}</td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{nf(r.ledger)}</td>
                          <td className="px-3 py-2 text-right font-semibold"
                              style={{ color: r.breach ? theme.wr : theme.t2 }}>
                            {r.shortfall > 0 ? `${nf(r.shortfall)} (${pf(r.shortfall_pct, 0)})` : DASH}
                          </td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.t2 }}>
                            {r.breach && r.estimated_value ? mf(r.estimated_value) : DASH}
                          </td>
                          <td className="px-3 py-2 text-xs" style={{ color: r.breach ? theme.wr : theme.t2 }}>
                            {r.breach ? 'not reaching us'
                              : r.settling ? 'this month, still settling'
                              : 'reconciles'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 space-y-1.5 text-xs" style={{ color: theme.t2 }}>
                  <p className="flex items-start gap-1.5">
                    <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    <span>
                      &quot;Worth roughly&quot; is the missing count at the average value of the
                      receipts that did arrive. It is an order of magnitude, not a figure to book.
                    </span>
                  </p>
                  <p>{recon.note}</p>
                  {recon.thresholds && (
                    <p>
                      A month is flagged only when it is short by at least{' '}
                      {nf(recon.thresholds.min_rows)} collections AND{' '}
                      {pf(recon.thresholds.min_pct, 0)} of what RealPay reported.
                    </p>
                  )}
                </div>
              </>
            )}
          </section>
        )}

        {/* ── The book, one panel per book ────────────────────────────── */}
        {book?.available === false && (
          <div className="rounded-lg px-3 py-2 text-sm" role="alert"
               style={{ background: theme.erB, color: theme.er }}>
            Data unavailable — the reporting copy could not be read ({book.reason}).
          </div>
        )}

        {book?.available && book.books && (
          <>
            <h2 className="text-base font-bold pt-1" style={{ color: theme.text }}>
              The book{' '}
              <span className="text-xs font-normal" style={{ color: theme.t2 }}>
                policies on cover, and how many are collecting. The two books are never added together.
              </span>
            </h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              {book.books.map(b => (
                <section key={b.book} className="p-4" style={card}>
                  <div className="flex items-baseline justify-between mb-1">
                    <h3 className="font-bold text-sm" style={{ color: theme.text }}>{b.book}</h3>
                    <span className="text-xs" style={{ color: theme.t2 }}>
                      {nf(b.paying)} of {nf(b.active)} collecting
                    </span>
                  </div>
                  <div className="flex items-baseline gap-2 mb-3">
                    <span className="text-3xl font-bold tabular-nums" style={{ color: theme.text }}>
                      {pf(b.paying_rate_pct)}
                    </span>
                    <span className="text-xs" style={{ color: theme.t2 }}>
                      of {nf(b.active)} on cover
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm tabular-nums">
                      <thead>
                        <tr style={{ background: NAVY, color: '#fff' }}>
                          <th className="text-left px-2 py-1.5 font-semibold">Product</th>
                          <th className="text-right px-2 py-1.5 font-semibold">On cover</th>
                          <th className="text-right px-2 py-1.5 font-semibold">Collecting</th>
                          <th className="text-right px-2 py-1.5 font-semibold">Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {b.products.map((p, i) => (
                          <tr key={p.product} style={{ background: i % 2 ? theme.g50 : 'transparent' }}>
                            <td className="px-2 py-1.5" style={{ color: theme.text }}>{p.product}</td>
                            <td className="px-2 py-1.5 text-right" style={{ color: theme.text }}>{nf(p.active)}</td>
                            <td className="px-2 py-1.5 text-right" style={{ color: theme.text }}>{nf(p.paying)}</td>
                            <td className="px-2 py-1.5 text-right font-semibold"
                                style={{ color: (p.paying_rate_pct ?? 100) < 20 ? theme.wr : theme.text }}>
                              {pf(p.paying_rate_pct)}
                            </td>
                          </tr>
                        ))}
                        <tr style={{ background: theme.g100, fontWeight: 700 }}>
                          <td className="px-2 py-1.5" style={{ color: theme.text }}>Total</td>
                          <td className="px-2 py-1.5 text-right" style={{ color: theme.text }}>{nf(b.active)}</td>
                          <td className="px-2 py-1.5 text-right" style={{ color: theme.text }}>{nf(b.paying)}</td>
                          <td className="px-2 py-1.5 text-right" style={{ color: theme.text }}>{pf(b.paying_rate_pct)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}
            </div>

            {/* ── Exceptions, on the SAME screen as the headline ───────── */}
            {book.exceptions && (
              <section className="p-4" style={card} aria-labelledby="exc-h">
                <h2 id="exc-h" className="text-base font-bold mb-1" style={{ color: theme.text }}>
                  Read these beside the numbers above
                </h2>
                <p className="text-xs mb-3" style={{ color: theme.t2 }}>
                  Counted inside the on-cover figure, not netted out of it. The brief requires them
                  on this screen, not on a separate tab.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  {[
                    { n: book.exceptions.expired_but_active, label: 'On cover, but the cover date has already passed',
                      why: 'Counted as on cover because the signed rule does not test the expiry date' },
                    { n: book.exceptions.payment_cancelled, label: 'Marked cancelled for payment, still on cover',
                      why: 'Two systems disagreeing about the same policy' },
                    { n: book.exceptions.no_expiry_date, label: 'No cover end date recorded at all',
                      why: 'Cannot be tested for expiry either way' },
                  ].map(x => (
                    <div key={x.label} className="rounded-xl p-3" style={{ background: theme.g100 }}>
                      <div className="text-2xl font-bold tabular-nums" style={{ color: theme.wr }}>{nf(x.n)}</div>
                      <div className="text-xs font-semibold mt-0.5" style={{ color: theme.text }}>{x.label}</div>
                      <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{x.why}</div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <footer className="text-[11px] leading-relaxed pt-2 pb-6 space-y-1" style={{ color: theme.t3 }}>
              <div className="flex items-center gap-1.5 font-semibold" style={{ color: theme.t2 }}>
                <Landmark className="w-3.5 h-3.5" /> How each figure is worked out
              </div>
              {book.rules && Object.values(book.rules).map(r => <div key={r}>· {r}</div>)}
              {book.paying_window && (
                <div>
                  · Collecting means at least one successful payment between{' '}
                  {dfmt(book.paying_window.from)} and {dfmt(book.paying_window.to)}.
                </div>
              )}
              {book.caveats?.map(c => <div key={c}>· {c}</div>)}
              <div>· Source: the Graphite read-only reporting copy. Nothing on this screen can change any record.</div>
            </footer>
          </>
        )}
      </main>
    </div>
  )
}
