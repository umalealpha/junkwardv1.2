'use client'

/**
 * The welcome-page band: "how much money we have in the morning to run the
 * operation" (CFO, 20-Sep-2026), above the KPI grid.
 *
 * It SELF-HIDES. `/banking/balances/` carries CanViewFinancials, so a viewer
 * who may not see it gets a 403 and this renders null — the same doctrine as
 * <CfoControlsSection/>, and the reason no client-side role list is guessed
 * here. It also renders null while the first read is in flight, so the shared
 * dashboard never flashes a finance band at someone who cannot have it.
 *
 * The full screen (per-account detail, the requests and batches behind "going
 * out") is at /banking/balances — this band links there rather than repeating it.
 */
import Link from 'next/link'
import { ArrowUpRight } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import type { UserProfile } from '@/lib/api'
import { BankBalancesPanel } from './BankBalancesPanel'
import { fromTheme } from './balancesPalette'
import { useBankBalances } from './useBankBalances'

export function BankBalancesSection({ me }: { me: UserProfile | null }) {
  const { theme, reduceMotion } = useTheme()
  // `has_profile === false` means the signed-in user has no profile row and so
  // holds no permission at all (api.ts, prod 2026-07-29). Don't spend a request
  // to be told that. Anything else asks the server.
  const enabled = me?.has_profile !== false
  const { data, loading, error, forbidden, reload } = useBankBalances(enabled)

  if (forbidden || !data) return null

  return (
    <BankBalancesPanel
      data={data}
      loading={loading}
      error={error}
      onRetry={reload}
      palette={fromTheme(theme)}
      layout="grid"
      reduceMotion={reduceMotion}
      headlineAction={
        <Link
          href="/banking/balances"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            color: '#FFFFFF', fontSize: 12, fontWeight: 700,
            textDecoration: 'underline', textUnderlineOffset: 3,
          }}
        >
          Open the full screen
          <ArrowUpRight size={13} aria-hidden="true" />
        </Link>
      }
    />
  )
}
