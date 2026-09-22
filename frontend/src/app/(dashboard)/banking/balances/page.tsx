'use client'

/**
 * Morning Bank Balances — the full screen.
 *
 * The same panel the welcome page shows, plus the detail behind the "going
 * out" figures on every card: what is still waiting in Omni (payment requests
 * at pending_finance / pending_cfo) and what has already gone to the bank and
 * is unconfirmed (FNB batch submissions). Each links into the screen that owns
 * it — /payment-requests and /banking/fnb — rather than re-listing the rows
 * here, so there is one place to work a payment and one place to read cash.
 *
 * Server-gated to finance / management (CanViewFinancials). A viewer without it
 * gets a 403 and this page says so in plain words instead of a blank screen —
 * unlike the dashboard band, which self-hides.
 */
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { BankBalancesPanel } from '@/components/finance/BankBalancesPanel'
import { fromTheme } from '@/components/finance/balancesPalette'
import { useBankBalances } from '@/components/finance/useBankBalances'
import { useTheme } from '@/contexts/ThemeContext'
import { getToken } from '@/lib/api'
import {
  FLOOR_LABEL, formatBwp, hasFigure, type BalanceAccount,
} from '@/lib/bankBalances'

export default function BankBalancesPage() {
  const router = useRouter()
  const { theme, reduceMotion } = useTheme()
  const [authed, setAuthed] = useState(false)
  const { data, loading, error, forbidden, reload } = useBankBalances(authed)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    setAuthed(true)
  }, [router])

  const p = fromTheme(theme)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Money in the bank"
        subtitle="What is there this morning, what is committed to go out, and the floor underneath"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Banking', href: '/banking' }, { label: 'Balances' }]}
        actions={
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={reload}
            disabled={loading}
          >
            Read again
          </Button>
        }
      />

      <main className="flex-1 p-4 sm:p-6 space-y-6" style={{ background: p.surface }}>
        {forbidden ? (
          <div
            className="rounded-xl p-6 max-w-xl"
            style={{ background: p.card, border: `1px solid ${p.cardBorder}`, color: p.ink }}
          >
            <h2 className="font-semibold text-base mb-1" style={{ color: p.heading }}>
              This screen is for finance and management
            </h2>
            <p className="text-sm" style={{ color: p.inkSoft }}>
              Bank balances are restricted. If you need them, ask the CFO to grant you
              financial access.
            </p>
          </div>
        ) : (
          <BankBalancesPanel
            data={data}
            loading={loading}
            error={error}
            onRetry={reload}
            palette={p}
            layout="grid"
            reduceMotion={reduceMotion}
            accountFooter={(account) => <AccountDetail account={account} />}
          />
        )}

        {data ? (
          <p className="text-xs max-w-3xl" style={{ color: p.inkSoft, lineHeight: 1.6 }}>
            <strong style={{ color: p.heading }}>{FLOOR_LABEL}</strong> is a floor, not a
            forecast. It is the balance less everything Omni knows is committed to leave —
            it does not include money coming in, because that never passes through Omni.
            An account showing no figure is not an account with no money: it is one Omni
            could not read, and the note on the card says why.
          </p>
        ) : null}
      </main>
    </div>
  )
}

/**
 * The detail behind "going out", per account. Counts and links only — the rows
 * themselves live on the screens that own them, and duplicating a payment
 * queue here would be a second place for it to go stale.
 */
function AccountDetail({ account }: { account: BalanceAccount }) {
  const { theme } = useTheme()
  const p = fromTheme(theme)
  const omni = Number(account.outgoing_omni)
  const bank = Number(account.outgoing_at_bank)
  if (!(omni > 0) && !(bank > 0)) {
    return (
      <p style={{ margin: 0, paddingTop: 8, borderTop: `1px solid ${p.line}`, fontSize: 12, color: p.inkSoft }}>
        Nothing is queued to leave this account.
      </p>
    )
  }
  return (
    <div style={{ paddingTop: 8, borderTop: `1px solid ${p.line}`, display: 'grid', gap: 8 }}>
      {omni > 0 ? (
        <DetailLink
          href="/payment-requests"
          title="Waiting in Omni"
          detail={`${formatBwp(account.outgoing_omni)} · raised and awaiting finance or CFO sign-off`}
          palette={p}
        />
      ) : null}
      {bank > 0 ? (
        <DetailLink
          href="/banking/fnb"
          title="At the bank, unconfirmed"
          detail={`${formatBwp(account.outgoing_at_bank)}${
            account.outgoing_unconfirmed_count > 0
              ? ` · ${account.outgoing_unconfirmed_count} batch${account.outgoing_unconfirmed_count === 1 ? '' : 'es'}`
              : ''
          } · sent to FNB, not yet acknowledged`}
          palette={p}
        />
      ) : null}
      {!hasFigure(account.balance) ? (
        <p style={{ margin: 0, fontSize: 11.5, color: p.inkSoft }}>
          These are still committed to leave even though the balance could not be read.
        </p>
      ) : null}
    </div>
  )
}

function DetailLink({
  href, title, detail, palette: p,
}: {
  href: string; title: string; detail: string
  palette: ReturnType<typeof fromTheme>
}) {
  return (
    <Link
      href={href}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, textDecoration: 'none',
        padding: '8px 8px', margin: '0 -8px', borderRadius: 8, color: p.ink,
      }}
      className="hover:opacity-80"
    >
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: 12.5, fontWeight: 700, color: p.heading }}>{title}</span>
        <span style={{ display: 'block', fontSize: 11.5, color: p.inkSoft, lineHeight: 1.4 }}>{detail}</span>
      </span>
      <ArrowUpRight size={14} aria-hidden="true" style={{ color: p.inkSoft, flexShrink: 0 }} />
    </Link>
  )
}
