'use client'

/**
 * Morning Bank Balances — the one panel, used in all three places.
 *
 * The CFO's ask, in his words: "how much money we have in the morning to run
 * the operation… the balances, what payments are loaded or pending in Omni, and
 * what the closing balances will be."
 *
 * Two things this file is not allowed to get wrong:
 *
 *  1. An account that cannot be read still APPEARS, clearly marked. "We can
 *     create the dashboard still and put those bank balances as zero. At least
 *     then I will know what is not rendering." A null balance renders a dashed
 *     placeholder and the server's own sentence — never "0.00", which is a
 *     CONFIRMED zero and looks completely different.
 *  2. The third figure is "Lowest you could be left with", never "closing
 *     balance". Omni knows what is committed to go out; it knows nothing about
 *     what is coming in. See FLOOR_LABEL in lib/bankBalances.ts.
 *
 * Renders from the contract shape alone — it never fetches. The three callers
 * (dashboard, /banking/balances, /app/balances) own the fetch and hand it in.
 */
import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Landmark, MinusCircle } from 'lucide-react'
import { useCountUp } from '@/hooks/useCountUp'
import {
  BALANCE_LABEL, FLOOR_LABEL, OUTGOING_BANK_LABEL, OUTGOING_LABEL, OUTGOING_OMNI_LABEL,
  formatBwp, gaboroneTime, hasFigure, needsAttention, readingLabel, statusMeta,
  type BalanceAccount, type BalanceGroup, type BankBalances, type StatusTone,
} from '@/lib/bankBalances'
import type { BalancesPalette } from './balancesPalette'

export type { BalancesPalette } from './balancesPalette'

// 8px rhythm. Nothing in this file uses a spacing value off this scale.
const S = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const

interface PanelProps {
  data: BankBalances | null
  loading?: boolean
  /** A plain sentence. Never a stack trace, never a fabricated figure. */
  error?: string | null
  palette: BalancesPalette
  /** 'single' = one full-width column (the phone). 'grid' = responsive columns. */
  layout?: 'grid' | 'single'
  /** The viewer's stored reduce-motion choice. CSS also honours the OS setting. */
  reduceMotion?: boolean
  /** Pin the headline to the top of the scroll (the phone). The CEO's
   *  five-second read must stay on screen while the accountant scrolls. */
  stickyHeadline?: boolean
  /** Small link row under the headline (e.g. "Open the full screen"). */
  headlineAction?: ReactNode
  /** Extra detail inside each card — the dedicated page passes the payment
   *  requests and bank batches behind the "going out" figures. */
  accountFooter?: (account: BalanceAccount) => ReactNode
  onRetry?: () => void
}

export function BankBalancesPanel({
  data, loading = false, error = null, palette: p, layout = 'grid',
  reduceMotion = false, stickyHeadline = false, headlineAction, accountFooter, onRetry,
}: PanelProps) {
  const attention = data ? needsAttention(data.headline) : false
  // Flat index across every group so the stagger reads as one sequence down
  // the panel, not as four sequences restarting per company.
  let cardIndex = 0

  return (
    <section aria-labelledby="bank-balances-heading" style={{ display: 'grid', gap: S.md }}>
      <PanelMotionStyles />

      <div
        className={reduceMotion ? undefined : 'bb-rise'}
        style={{
          position: stickyHeadline ? 'sticky' : 'relative', top: stickyHeadline ? 0 : undefined,
          zIndex: stickyHeadline ? 5 : undefined,
          overflow: 'hidden', borderRadius: stickyHeadline ? '0 0 16px 16px' : 16,
          background: p.navy, color: p.onNavy,
          padding: stickyHeadline
            ? `${S.md}px ${S.md}px ${S.md}px`
            : `${S.lg}px ${S.lg}px ${S.md + S.xs}px`,
          // The attention state is a 1px accent RING, never accent-coloured
          // text: orange or grey on this block fails contrast in the
          // professional theme (globals.css remaps the brand hexes).
          boxShadow: attention ? `inset 0 0 0 1px ${p.accent}` : 'none',
        }}
      >
        <span aria-hidden="true" style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: `radial-gradient(520px 260px at 92% -20%, ${p.accent}22, transparent 60%)`,
        }} />
        <div style={{ position: 'relative' }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: S.sm, marginBottom: S.sm,
            fontSize: 10, letterSpacing: '0.22em', textTransform: 'uppercase',
            fontWeight: 700, color: p.onNavy, opacity: 0.66,
          }}>
            <Landmark size={13} aria-hidden="true" />
            <span>Money in the bank</span>
            {data?.headline.taken_at ? (
              <span style={{ opacity: 0.8 }}>· as at {gaboroneTime(data.headline.taken_at)}</span>
            ) : null}
          </div>

          <h2 id="bank-balances-heading" style={{
            margin: 0, fontFamily: p.headingFont, fontWeight: 700,
            fontSize: 'clamp(19px, 2.4vw, 30px)', lineHeight: 1.18,
            letterSpacing: '0.1px', color: p.onNavy,
            fontVariantNumeric: 'tabular-nums lining-nums',
          }}>
            {loading && !data ? 'Reading the bank…'
              : error && !data ? 'The balances could not be read just now'
                : data ? data.headline.sentence
                  : 'No balances to show'}
          </h2>

          {data ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: S.sm, marginTop: S.md }}>
              <HeadlineChip
                palette={p}
                tone={attention ? 'warn' : 'ok'}
                text={`${data.headline.accounts_fresh} of ${data.headline.accounts_total} accounts up to date`}
              />
              {attention ? (
                <HeadlineChip palette={p} tone="warn" text={worstStatusPhrase(data)} />
              ) : null}
              {headlineAction}
            </div>
          ) : null}

          {error ? (
            <p style={{ margin: `${S.md}px 0 0`, fontSize: 13, color: p.onNavy, opacity: 0.85 }}>
              {error}{' '}
              {onRetry ? (
                <button onClick={onRetry} style={{
                  background: 'none', border: 0, padding: 0, font: 'inherit', cursor: 'pointer',
                  color: p.onNavy, fontWeight: 700, textDecoration: 'underline',
                }}>Try again</button>
              ) : null}
            </p>
          ) : null}
        </div>
      </div>

      {loading && !data ? (
        <div style={{ display: 'grid', gap: S.md, gridTemplateColumns: columns(layout) }}>
          {[0, 1, 2].map((i) => (
            <div key={i} aria-hidden="true" style={{
              height: 188, borderRadius: 14, background: p.card,
              border: `1px solid ${p.cardBorder}`, opacity: 0.6,
            }} />
          ))}
        </div>
      ) : null}

      {data?.groups.map((group) => (
        <div key={group.company} style={{ display: 'grid', gap: S.sm }}>
          <GroupHeading group={group} palette={p} />
          <div style={{ display: 'grid', gap: S.md, gridTemplateColumns: columns(layout) }}>
            {group.accounts.map((account) => (
              <AccountCard
                key={account.id}
                account={account}
                palette={p}
                index={cardIndex++}
                reduceMotion={reduceMotion}
                footer={accountFooter?.(account)}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

// ─── Pieces ────────────────────────────────────────────────────────────────

function columns(layout: 'grid' | 'single'): string {
  // Single column on the phone; on desktop the cards find their own number of
  // columns without a breakpoint, and never squeeze below a readable 280px.
  //
  // auto-FILL, not auto-fit: three of the four companies own a single account,
  // and auto-fit collapses the empty tracks so that one card stretches the full
  // width of the page. A lone 1,200px-wide card holding the words "No figure"
  // looked like a rendering fault. auto-fill keeps the track, so every card is
  // the same size whichever company it belongs to.
  return layout === 'single' ? '1fr' : 'repeat(auto-fill, minmax(280px, 1fr))'
}

function worstStatusPhrase(data: BankBalances): string {
  switch (data.headline.worst_status) {
    case 'failed': return 'A reading failed this morning'
    case 'never_read': return 'Some accounts are not being read yet'
    case 'no_balance': return 'A bank sent no balance'
    case 'stale': return 'Some readings are more than 8 hours old'
    default: return 'Not every account reported'
  }
}

function HeadlineChip({ palette: p, tone, text }: { palette: BalancesPalette; tone: 'ok' | 'warn'; text: string }) {
  const Icon = tone === 'ok' ? CheckCircle2 : AlertTriangle
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: S.xs + 2,
      padding: `${S.xs + 1}px ${S.sm + 2}px`, borderRadius: 999,
      // On the dark block everything is white — tone is carried by the icon and
      // the ring, not by a colour that would drop below contrast.
      color: p.onNavy, background: 'rgba(255,255,255,0.10)',
      border: `1px solid ${tone === 'ok' ? 'rgba(255,255,255,0.22)' : p.accent}`,
      fontSize: 12, fontWeight: 600, letterSpacing: '0.01em',
    }}>
      <Icon size={13} aria-hidden="true" />
      {text}
    </span>
  )
}

function GroupHeading({ group, palette: p }: { group: BalanceGroup; palette: BalancesPalette }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: S.sm, padding: `${S.sm}px ${S.xs}px 0` }}>
      <h3 style={{
        margin: 0, fontFamily: p.headingFont, fontSize: 13, fontWeight: 700,
        letterSpacing: '0.12em', textTransform: 'uppercase', color: p.inkSoft,
      }}>{group.company}</h3>
      <span aria-hidden="true" style={{ flex: 1, height: 1, background: p.line }} />
      <span style={{
        fontSize: 12.5, color: p.inkSoft, fontVariantNumeric: 'tabular-nums lining-nums',
      }}>
        {hasFigure(group.subtotal_balance)
          ? formatBwp(group.subtotal_balance)
          : 'subtotal unavailable'}
      </span>
    </div>
  )
}

function toneColour(tone: StatusTone, p: BalancesPalette): { fg: string; bg: string } {
  if (tone === 'danger') return { fg: p.danger, bg: p.dangerBg }
  if (tone === 'warn') return { fg: p.warn, bg: p.warnBg }
  return { fg: p.inkSoft, bg: 'transparent' }
}

function AccountCard({
  account, palette: p, index, reduceMotion, footer,
}: {
  account: BalanceAccount
  palette: BalancesPalette
  index: number
  reduceMotion: boolean
  footer?: ReactNode
}) {
  const meta = statusMeta(account.balance_status)
  const tone = toneColour(meta.tone, p)
  // Narrowed to a string by hasFigure(), so the JSX below can hand it straight
  // to <BigFigure/> without a cast.
  const balance = hasFigure(account.balance) ? account.balance : null
  const note = account.note || meta.fallbackNote

  return (
    <article
      className={reduceMotion ? undefined : 'bb-rise'}
      style={{
        // 40ms apart, as asked. The CSS class carries the keyframes; only the
        // delay varies per card.
        animationDelay: reduceMotion ? undefined : `${index * 40}ms`,
        position: 'relative', overflow: 'hidden',
        background: p.card, border: `1px solid ${p.cardBorder}`, boxShadow: p.cardShadow,
        borderRadius: 14, padding: S.md, display: 'grid', gap: S.sm,
      }}
    >
      {/* A 2px rail in the account's own status colour — colour carrying
          meaning, not decoration. */}
      <span aria-hidden="true" style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 2,
        background: meta.tone === 'muted' ? p.accent : tone.fg, opacity: meta.tone === 'muted' ? 0.45 : 1,
      }} />

      <header style={{ display: 'flex', alignItems: 'baseline', gap: S.sm }}>
        <h4 style={{
          margin: 0, flex: 1, minWidth: 0, fontFamily: p.headingFont,
          fontSize: 14.5, fontWeight: 700, color: p.heading, lineHeight: 1.25,
        }}>{account.label}</h4>
        <span style={{ fontSize: 12, color: p.inkSoft, fontVariantNumeric: 'tabular-nums' }}>
          {account.account_masked}
        </span>
      </header>

      <div>
        <FigureLabel palette={p}>{BALANCE_LABEL}</FigureLabel>
        {balance !== null
          ? <BigFigure value={balance} strike={meta.strike} palette={p} reduceMotion={reduceMotion} />
          : <MissingFigure tone={tone} />}
        <p style={{ margin: `${S.xs}px 0 0`, fontSize: 12, color: tone.fg, fontWeight: meta.tone === 'muted' ? 400 : 600 }}>
          {readingLabel(account)}
        </p>
        {note ? (
          <p style={{
            margin: `${S.sm}px 0 0`, fontSize: 12.5, lineHeight: 1.45,
            color: tone.fg, background: tone.bg === 'transparent' ? undefined : tone.bg,
            border: tone.bg === 'transparent' ? undefined : `1px solid ${tone.fg}33`,
            borderRadius: 8, padding: tone.bg === 'transparent' ? 0 : `${S.sm - 2}px ${S.sm}px`,
          }}>{note}</p>
        ) : null}
      </div>

      <div style={{ borderTop: `1px solid ${p.line}`, paddingTop: S.sm }}>
        <FigureLabel palette={p}>{OUTGOING_LABEL}</FigureLabel>
        <MoneyRow palette={p} label={OUTGOING_OMNI_LABEL} value={account.outgoing_omni} />
        <MoneyRow
          palette={p}
          label={account.outgoing_unconfirmed_count > 0
            ? `${OUTGOING_BANK_LABEL} (${account.outgoing_unconfirmed_count})`
            : OUTGOING_BANK_LABEL}
          value={account.outgoing_at_bank}
        />
      </div>

      <div style={{ borderTop: `1px solid ${p.line}`, paddingTop: S.sm }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: S.sm }}>
          <span style={{ flex: 1, fontSize: 12.5, color: p.inkSoft, lineHeight: 1.3 }}>{FLOOR_LABEL}</span>
          <span style={{
            fontSize: 15, fontWeight: 700, whiteSpace: 'nowrap',
            fontVariantNumeric: 'tabular-nums lining-nums',
            color: hasFigure(account.projected_floor)
              ? (Number(account.projected_floor) < 0 ? p.danger : p.heading)
              : p.inkSoft,
          }}>
            {hasFigure(account.projected_floor) ? formatBwp(account.projected_floor) : '—'}
          </span>
        </div>
        {!hasFigure(account.projected_floor) ? (
          <p style={{ margin: `${S.xs}px 0 0`, fontSize: 11.5, color: p.inkSoft }}>
            Needs the bank balance before this can be worked out.
          </p>
        ) : null}
      </div>

      {footer}
    </article>
  )
}

function FigureLabel({ palette: p, children }: { palette: BalancesPalette; children: ReactNode }) {
  return (
    <div style={{
      fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase',
      fontWeight: 700, color: p.inkSoft, marginBottom: S.xs,
    }}>{children}</div>
  )
}

/** The one animated number on the card. Count-up on the balance only. */
function BigFigure({
  value, strike, palette: p, reduceMotion,
}: { value: string; strike: boolean; palette: BalancesPalette; reduceMotion: boolean }) {
  const target = Number(value)
  const animate = isFinite(target) && !reduceMotion
  const counted = useCountUp(target, 900, animate)
  return (
    <div style={{
      fontFamily: p.headingFont, fontWeight: 700, color: p.heading,
      fontSize: 'clamp(22px, 2.1vw, 28px)', lineHeight: 1.1, whiteSpace: 'nowrap',
      fontVariantNumeric: 'tabular-nums lining-nums',
      textDecoration: strike ? 'line-through' : undefined,
      textDecorationThickness: strike ? '1px' : undefined,
      opacity: strike ? 0.72 : 1,
    }}>
      {formatBwp(isFinite(target) ? counted : value)}
    </div>
  )
}

/**
 * A balance we do not have. Deliberately nothing like a number: a dashed slot,
 * a struck-through circle and the words. A confirmed "BWP 0.00" next to this
 * can never be mistaken for it.
 */
function MissingFigure({ tone }: { tone: { fg: string; bg: string } }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: S.sm,
      border: `1px dashed ${tone.fg}`, borderRadius: 10,
      padding: `${S.sm - 2}px ${S.sm + 2}px`, color: tone.fg,
      fontSize: 14.5, fontWeight: 700, lineHeight: 1.2,
    }}>
      <MinusCircle size={16} aria-hidden="true" />
      <span>No figure</span>
    </div>
  )
}

function MoneyRow({ palette: p, label, value }: { palette: BalancesPalette; label: string; value: string }) {
  const zero = Number(value) === 0
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: S.sm, marginTop: S.xs }}>
      <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: p.inkSoft, lineHeight: 1.35 }}>{label}</span>
      <span style={{
        fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap',
        fontVariantNumeric: 'tabular-nums lining-nums',
        color: zero ? p.inkSoft : p.ink,
      }}>{formatBwp(value)}</span>
    </div>
  )
}

/**
 * Motion, scoped to this panel. Cards fade up ~40ms apart on mount; the balance
 * counts up. Honoured twice over: the `reduceMotion` prop drops the class and
 * the count-up, and the media query below covers the OS setting for anyone who
 * has not set the in-app preference.
 */
function PanelMotionStyles() {
  return (
    <style>{`
      .bb-rise { animation: bb-rise 360ms cubic-bezier(0.16, 1, 0.3, 1) both; }
      @keyframes bb-rise {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: none; }
      }
      @media (prefers-reduced-motion: reduce) {
        .bb-rise { animation: none; opacity: 1; transform: none; }
      }
    `}</style>
  )
}
