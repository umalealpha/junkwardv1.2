/**
 * Fixtures for the Morning Bank Balances screens.
 *
 * These exist so the panel can be developed, tested and LOOKED AT before the
 * endpoint is live. They are never a fallback: no screen may render a fixture
 * when the real call fails — showing the CFO an invented balance is worse than
 * showing him nothing. The dashboard/banking pages only reach for these behind
 * `process.env.NODE_ENV === 'development'` and an explicit `?mock=` in the URL,
 * so a production bundle cannot route here.
 *
 * `mixed` is the real state of prod on 20-Sep-2026 per the contract: three
 * Alpha Direct accounts read daily (one of them reporting a FALSE zero the
 * backend now surfaces as no_balance), and three accounts never read at all.
 */
import type { BankBalances, BalanceStatus } from '@/lib/bankBalances'

const NEVER_READ = {
  balance: null,
  balance_status: 'never_read' as BalanceStatus,
  taken_at: null,
  age_hours: null,
  outgoing_omni: '0.00',
  outgoing_at_bank: '0.00',
  outgoing_unconfirmed_count: 0,
  projected_floor: null,
  note: 'Not yet being read from the bank.',
}

/** The state of prod today: 3 of 6 accounts read. */
export const mixedBalances: BankBalances = {
  headline: {
    total_balance: '2184302.61',
    accounts_read: 3, accounts_fresh: 3,
    accounts_total: 6,
    worst_status: 'never_read',
    taken_at: '2026-09-20T04:04:12Z',
    sentence: 'Cash in the six accounts: BWP 2,184,302.61 — 3 of 6 accounts read',
  },
  groups: [
    {
      company: 'Alpha Direct',
      subtotal_balance: null,
      accounts: [
        {
          id: 'a1', label: 'Alpha Direct — Current', account_masked: '…2335',
          balance: '995103.12', balance_status: 'ok',
          taken_at: '2026-09-20T04:04:12Z', age_hours: 3.2,
          outgoing_omni: '47487.50', outgoing_at_bank: '1727536.87',
          outgoing_unconfirmed_count: 29,
          projected_floor: '-779921.25', note: '',
        },
        {
          id: 'a2', label: 'Alpha Direct — Claims', account_masked: '…2265',
          balance: null, balance_status: 'no_balance',
          taken_at: '2026-09-20T04:04:18Z', age_hours: 3.2,
          outgoing_omni: '212400.00', outgoing_at_bank: '0.00',
          outgoing_unconfirmed_count: 0,
          projected_floor: null,
          note: 'The bank answered but sent no balance for this account.',
        },
        {
          id: 'a3', label: 'Alpha Direct — Call', account_masked: '…9485',
          balance: '1189199.49', balance_status: 'ok',
          taken_at: '2026-09-20T04:04:21Z', age_hours: 3.2,
          outgoing_omni: '0.00', outgoing_at_bank: '0.00',
          outgoing_unconfirmed_count: 0,
          projected_floor: '1189199.49', note: '',
        },
      ],
    },
    {
      company: 'Veritas',
      subtotal_balance: null,
      accounts: [{ id: 'v1', label: 'Veritas — Current', account_masked: '…3132', ...NEVER_READ }],
    },
    {
      company: 'Risk Software',
      subtotal_balance: null,
      accounts: [{ id: 'r1', label: 'Risk Software — Current', account_masked: '…4999', ...NEVER_READ }],
    },
    {
      company: 'Unicoin',
      subtotal_balance: null,
      accounts: [{ id: 'u1', label: 'Unicoin — Current', account_masked: '…1725', ...NEVER_READ }],
    },
  ],
}

/** Every account healthy — the state the morning pull is meant to reach. */
export const healthyBalances: BankBalances = {
  headline: {
    total_balance: '3412880.44',
    accounts_read: 6, accounts_fresh: 6,
    accounts_total: 6,
    worst_status: 'ok',
    taken_at: '2026-09-20T04:04:12Z',
    sentence: 'Cash in the six accounts: BWP 3,412,880.44 — all 6 accounts read',
  },
  groups: mixedBalances.groups.map((g) => ({
    ...g,
    subtotal_balance: '1000000.00',
    accounts: g.accounts.map((a, i) => ({
      ...a,
      balance: i === 1 ? '0.00' : '1000000.00',   // a CONFIRMED zero, not an unknown
      balance_status: 'ok' as BalanceStatus,
      taken_at: '2026-09-20T04:04:12Z',
      age_hours: 3.2,
      projected_floor: i === 1 ? '-212400.00' : '952512.50',
      note: '',
    })),
  })),
}

/** Every failure mode at once — stale, failed, no_balance, never_read. */
export const troubledBalances: BankBalances = {
  headline: {
    total_balance: '995103.12',
    accounts_read: 2, accounts_fresh: 2,
    accounts_total: 6,
    worst_status: 'failed',
    taken_at: '2026-09-18T04:04:12Z',
    sentence: 'Cash in the six accounts: BWP 995,103.12 — 2 of 6 accounts read, and one reading failed',
  },
  groups: [
    {
      company: 'Alpha Direct',
      subtotal_balance: null,
      accounts: [
        {
          ...mixedBalances.groups[0].accounts[0],
          balance_status: 'stale', age_hours: 19.4,
          note: 'This reading is from yesterday evening.',
        },
        {
          ...mixedBalances.groups[0].accounts[1],
          balance: '48210.00', balance_status: 'failed',
          taken_at: '2026-09-18T04:04:18Z', age_hours: 51.2,
          projected_floor: '-164190.00',
          note: 'This morning’s read failed — the figure below is from two days ago.',
        },
        { ...mixedBalances.groups[0].accounts[2], ...NEVER_READ, id: 'a3' },
      ],
    },
    ...mixedBalances.groups.slice(1),
  ],
}

/** Nothing could be read at all — the whole pull is down. */
export const nothingRead: BankBalances = {
  headline: {
    total_balance: null,
    accounts_read: 0, accounts_fresh: 0,
    accounts_total: 6,
    worst_status: 'failed',
    taken_at: null,
    sentence: 'No balance could be read from any of the six accounts this morning',
  },
  groups: mixedBalances.groups.map((g) => ({
    ...g,
    subtotal_balance: null,
    accounts: g.accounts.map((a) => ({
      ...a, ...NEVER_READ,
      balance_status: 'failed' as BalanceStatus,
      note: 'The bank connection failed this morning.',
    })),
  })),
}

export const mockScenarios = {
  mixed: mixedBalances,
  healthy: healthyBalances,
  troubled: troubledBalances,
  none: nothingRead,
} as const

export type MockScenario = keyof typeof mockScenarios
