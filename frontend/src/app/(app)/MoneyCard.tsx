'use client'
/**
 * MoneyCard — "how much money we have to run the operation", on the phone's
 * home screen.
 *
 * CFO, 20-Sep-2026, after finding the balances one tap away in the Work hub:
 * *"Where did you put the bank balance in the app, the complete info,
 * balances - pending payments = theoretical balance"*.
 *
 * So it is three numbers in HIS order, on the first screen, above the fold:
 *
 *     In the bank            what the bank says we hold
 *   − Going out              raised in Omni + already in the bank's queue
 *   = Theoretical balance    what is left if every one of them clears
 *
 * THE THIRD NUMBER IS A FLOOR, NOT A FORECAST, and the label says so. It
 * subtracts everything committed and adds nothing coming in, because Omni does
 * not know what is coming in. Calling it a "closing balance" would be a
 * different and much worse number.
 *
 * It never invents a figure. When an account cannot be read the card says how
 * many of the six were read and shows an amber note, rather than quietly
 * totalling the ones that worked and presenting that as the cash position —
 * three separate false-zero bugs on this feature in one evening (19-Sep) all
 * had the same shape: a confident number standing in for a missing one.
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronRight, Landmark } from 'lucide-react'
import { afetch } from './api'
import { C, card, serif } from './ui'

interface Headline {
  total_balance: string | null
  total_outgoing_omni: string
  total_outgoing_at_bank: string
  total_outgoing: string
  outgoing_unconfirmed_count: number
  total_projected_floor: string | null
  accounts_read: number
  accounts_fresh: number
  accounts_total: number
  worst_status: string
  taken_at: string | null
}

const pula = (v: string | null) =>
  v === null ? null : `P ${Number(v).toLocaleString('en-BW', {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

/** "taken 06:04 today" — from the server's stamp, never the browser clock. */
function takenLabel(iso: string | null): string {
  if (!iso) return ''
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return ''
  const hhmm = t.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Gaborone' })
  const sameDay = t.toDateString() === new Date().toDateString()
  return sameDay ? `Read ${hhmm} today` : `Read ${hhmm} on ${t.toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', timeZone: 'Africa/Gaborone' })}`
}

const MOCKS: Record<string, Headline> = process.env.NODE_ENV === 'production' ? {} : {
  // The live figures on 20-Sep-2026, three of six accounts readable.
  mixed: { total_balance: '2531227.36', total_outgoing_omni: '47487.50',
    total_outgoing_at_bank: '1727536.87', total_outgoing: '1775024.37',
    outgoing_unconfirmed_count: 29, total_projected_floor: '756202.99',
    accounts_read: 3, accounts_fresh: 3, accounts_total: 6, worst_status: 'failed',
    taken_at: new Date().toISOString() },
  // Everything readable, nothing going out.
  clean: { total_balance: '2531227.36', total_outgoing_omni: '0.00',
    total_outgoing_at_bank: '0.00', total_outgoing: '0.00',
    outgoing_unconfirmed_count: 0, total_projected_floor: '2531227.36',
    accounts_read: 6, accounts_fresh: 6, accounts_total: 6, worst_status: 'ok',
    taken_at: new Date().toISOString() },
  // Every account carries a figure, but three are yesterday's. This is the
  // state the card used to render as a clean, complete, silent total.
  stale: { total_balance: '2531227.36', total_outgoing_omni: '48947.53',
    total_outgoing_at_bank: '486835.61', total_outgoing: '535783.14',
    outgoing_unconfirmed_count: 10, total_projected_floor: '1995444.22',
    accounts_read: 6, accounts_fresh: 3, accounts_total: 6, worst_status: 'failed',
    taken_at: new Date(Date.now() - 36 * 3600 * 1000).toISOString() },
  // BOTH failures at once: two accounts stale, one never read. The old
  // wording dropped the staleness here and claimed "only 3 of 6 could be
  // read" over a total summing five.
  mixedfail: { total_balance: '2100000.00', total_outgoing_omni: '48947.53',
    total_outgoing_at_bank: '486835.61', total_outgoing: '535783.14',
    outgoing_unconfirmed_count: 10, total_projected_floor: '1564216.86',
    accounts_read: 5, accounts_fresh: 3, accounts_total: 6, worst_status: 'failed',
    taken_at: new Date(Date.now() - 36 * 3600 * 1000).toISOString() },
  // Nothing readable at all — the floor must be "No figure", never 0.00.
  none: { total_balance: null, total_outgoing_omni: '47487.50',
    total_outgoing_at_bank: '1727536.87', total_outgoing: '1775024.37',
    outgoing_unconfirmed_count: 29, total_projected_floor: null,
    accounts_read: 0, accounts_fresh: 0, accounts_total: 6, worst_status: 'failed',
    taken_at: null },
  // More committed than we hold.
  negative: { total_balance: '500000.00', total_outgoing_omni: '47487.50',
    total_outgoing_at_bank: '1727536.87', total_outgoing: '1775024.37',
    outgoing_unconfirmed_count: 29, total_projected_floor: '-1275024.37',
    accounts_read: 6, accounts_fresh: 6, accounts_total: 6, worst_status: 'ok',
    taken_at: new Date().toISOString() },
}

export default function MoneyCard() {
  const [h, setH] = useState<Headline | null>(null)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    // Dev-only fixtures so the card can be LOOKED AT without a live API.
    // Dropped from the production bundle by the NODE_ENV branch.
    if (process.env.NODE_ENV !== 'production') {
      const mock = new URLSearchParams(window.location.search).get('mock')
      if (mock) { setH(MOCKS[mock] ?? MOCKS.mixed); return }
    }
    afetch<{ headline: Headline }>('/banking/balances/')
      .then(r => setH(r.headline))
      // Not everyone can see the company's cash. A refusal hides the card
      // entirely rather than leaving an empty shell on a shared home screen.
      .catch(() => setDenied(true))
  }, [])

  if (denied || !h) return null

  // "Complete" means every account was read TODAY, not merely that every
  // account has a number against it. A failed read falls back to an older good
  // figure, so `accounts_read` can reach six while half of them are days old —
  // and the old test (`accounts_read === accounts_total`) would then hide the
  // warning and present stale cash as the morning position. `accounts_fresh`
  // counts only accounts whose latest read actually succeeded.
  // A backend that predates this field sends no accounts_fresh, and the
  // deploy script ships backend and frontend separately — so this browser can
  // hold the new bundle against the old API for minutes. Without the guard,
  // `read - undefined` is NaN, the card renders "Only undefined of 6 accounts"
  // and pins its warning on through a perfectly clean morning.
  const fresh = Number.isFinite(h.accounts_fresh) ? h.accounts_fresh : h.accounts_read
  const complete = fresh === h.accounts_total
  const stale = h.accounts_read - fresh          // figure, but not current
  const missing = h.accounts_total - h.accounts_read   // no figure at all
  const bank = pula(h.total_balance)
  const floor = pula(h.total_projected_floor)

  return (
    <Link
      href="/app/balances"
      className="oa-press oa-rise oa-rise-1"
      style={{ ...card, display: 'block', padding: 16, textDecoration: 'none',
               color: C.ink, borderLeft: `4px solid ${C.orange}` }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <span style={{ display: 'grid', placeItems: 'center', width: 44, height: 44,
                       borderRadius: 14, background: C.navy }}>
          <Landmark size={22} color={C.orange} />
        </span>
        <span style={{ flex: 1 }}>
          <span style={{ display: 'block', fontFamily: serif, fontWeight: 700, fontSize: 17 }}>
            Money in the bank
          </span>
          <span style={{ display: 'block', fontSize: 12, color: C.inkSoft }}>
            {takenLabel(h.taken_at)}
          </span>
        </span>
        <ChevronRight size={18} color={C.inkSoft} />
      </div>

      <Line label="In the bank" value={bank} big />
      <Line label="Going out" value={`− ${pula(h.total_outgoing)}`} muted />
      <div style={{ paddingLeft: 2, marginTop: -4, marginBottom: 8 }}>
        <Sub label="waiting in Omni" value={pula(h.total_outgoing_omni)} />
        <Sub
          label={h.outgoing_unconfirmed_count
            ? `at the bank, ${h.outgoing_unconfirmed_count} unconfirmed`
            : 'at the bank'}
          value={pula(h.total_outgoing_at_bank)}
        />
      </div>
      {/* Stacked, not a label/value row: at 375px "Lowest you could be left
          with" wraps to two lines and shoves the figure into a third, which
          splits the "P" from its own number. Seen at phone width, not guessed. */}
      <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
        <div style={{ fontSize: 13, color: C.inkSoft }}>
          Lowest you could be left with
        </div>
        <div style={{ fontSize: 26, fontWeight: 700, color: C.navy,
                      fontVariantNumeric: 'tabular-nums', lineHeight: 1.15,
                      marginTop: 2, whiteSpace: 'nowrap' }}>
          {floor ?? 'No figure'}
        </div>
      </div>

      {!complete && (
        <div style={{ marginTop: 12, fontSize: 12, color: C.amber, lineHeight: 1.45 }}>
          {/* TWO independent failures, and they can happen at once. An
              earlier version fired the stale wording only when every account
              had some figure, so the mixed case — two accounts stale, one
              never read — printed "only 3 of 6 could be read" above a total
              summing FIVE accounts, and dropped the staleness entirely. State
              whichever facts are true, both if both are. */}
          {[
            missing > 0
              ? `${missing} of ${h.accounts_total} accounts could not be read`
              : '',
            stale > 0
              ? `${stale} ${missing > 0 ? 'more ' : `of ${h.accounts_total} accounts `}`
                + `${stale === 1 ? 'is' : 'are'} showing an older balance`
              : '',
          ].filter(Boolean).join(', and ')}, so this is not the whole picture.
          Tap to see which.
        </div>
      )}
    </Link>
  )
}

function Line({ label, value, big, muted, accent }: {
  label: string; value: string | null; big?: boolean; muted?: boolean; accent?: boolean
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                  gap: 12, padding: '4px 0' }}>
      <span style={{ fontSize: big ? 14 : 13, color: muted ? C.inkSoft : C.ink }}>{label}</span>
      <span style={{
        fontSize: big ? 22 : 15, fontWeight: big ? 700 : 600,
        fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap',
        color: accent ? C.navy : muted ? C.inkSoft : C.ink,
      }}>
        {/* A missing figure is never rendered as 0.00 — that is the whole
            lesson of this feature. */}
        {value ?? 'No figure'}
      </span>
    </div>
  )
}

function Sub({ label, value }: { label: string; value: string | null }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12,
                  fontSize: 12, color: C.inkSoft, padding: '2px 0' }}>
      <span>{label}</span>
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{value ?? '—'}</span>
    </div>
  )
}
