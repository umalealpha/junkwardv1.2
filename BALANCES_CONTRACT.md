# Morning Bank Balances — the API contract

`GET /api/v1/banking/balances/` · permission `CanViewFinancials` · **read-only, GET only, forever**

This file is the agreement between the backend and the screen. Neither side changes
it alone. Delete it once both sides have shipped.

```jsonc
{
  "headline": {
    "total_balance": "2184302.61",   // string decimal, or null if NOTHING could be read
    "accounts_read": 3,              // how many returned a real figure
    "accounts_fresh": 3,             // how many of those are CURRENT — an account
                                     // falling back to an older good reading counts
                                     // in accounts_read but not here. The screens
                                     // warn on this one, never on accounts_read.
    "accounts_total": 6,
    "worst_status": "never_read",    // the most serious status across all accounts
    "taken_at": "2026-09-20T04:04:12Z",  // the OLDEST reading in the set, or null
    "sentence": "Cash in the six accounts: BWP 2,184,302.61 — 3 of 6 accounts read"
  },
  "groups": [
    {
      "company": "Alpha Direct",
      "subtotal_balance": "2184302.61",   // null if any account in the group is unknown
      "accounts": [
        {
          "id": "uuid",
          "label": "Alpha Direct — Current",     // plain words, never the raw account_name
          "account_masked": "…2335",             // NEVER the full number
          "balance": "995103.12",                // string decimal, or null when unknown
          "balance_status": "ok",                // see the table below
          "taken_at": "2026-09-20T04:04:12Z",    // null when never read
          "age_hours": 3.2,                      // null when never read
          "outgoing_omni": "47487.50",           // raised, not yet through Omni
          "outgoing_at_bank": "1727536.87",      // sent to the bank, unconfirmed
          "outgoing_unconfirmed_count": 29,
          "projected_floor": "409278.75",        // balance − both outgoings; null if balance null
          "note": "Never read from the bank."    // one plain sentence, or ""
        }
      ]
    }
  ]
}
```

## `balance_status` — the only five values

| value | meaning | `balance` | what the screen does |
|---|---|---|---|
| `ok` | read within the last 8 hours | a figure | show it, muted timestamp |
| `stale` | last good read is 8–26 hours old | a figure | show it, amber timestamp |
| `failed` | the most recent attempt failed; an older figure exists | the OLDER figure | strike it through lightly, red note |
| `no_balance` | the bank answered but sent no balance block | **null** | no number — "the bank sent no balance" |
| `never_read` | this account has never been read | **null** | no number — "not yet being read from the bank" |

**`balance` is `null` whenever the figure is unknown. It is NEVER 0.00 to mean "unknown".**
A real zero is `"0.00"` with status `ok`. This distinction is the entire point: today
the Claims account reports a false `0.00` because `fnb/statements.py` defaults
`closing_balance` to `0.00` and only overwrites it when the bank sends a CLBD block,
so "no balance returned" and "genuinely empty" are indistinguishable.

**The CFO explicitly asked that unreadable accounts still appear, showing zero, so he
can see what is not rendering.** Honour that by always returning the account row with
`balance: null` and a `note` saying why — the screen renders the placeholder. Never
drop an account from the response.

## The six accounts, and their real state today (verified on prod 20-Sep-2026)

| label | account_number | company | today |
|---|---|---|---|
| Alpha Direct — Current | 62403392335 | Alpha Direct | read daily, works |
| Alpha Direct — Claims | 62493282265 | Alpha Direct | read daily, **false 0.00** |
| Alpha Direct — Call | 62407809485 | Alpha Direct | read daily, works |
| Veritas — Current | 62477843132 | Veritas | **never read** |
| Risk Software — Current | 62477854999 | Risk Software | **never read** |
| Unicoin — Current | 62842621725 | Unicoin | **never read** |

`4901344312871000` (FNBB Credit Card Control A/C) is a CARD, not a bank account. It is
in the daily pull list today and fails with HTTP 400 every morning. It must NOT be
watched and must NOT appear.

## Where the outgoing figures come from

- `outgoing_omni` — `taskboard.PaymentRequest` with `status in ('pending_finance','pending_cfo')`,
  sum of `total`.
- `outgoing_at_bank` — `fnb.FNBBatchSubmission` with `status in ('submitted','acknowledged','unknown')`,
  sum of `total_amount_bwp`, grouped by `source_account`.
- Never include `settled` batches — that money is already inside the bank's own balance,
  and subtracting it again is a double count.
- `projected_floor` is a FLOOR, not a forecast. It excludes money coming in, which Omni
  does not know. The screen must never label it "closing balance".

Rounding: `ROUND_HALF_UP`, 2 places, everywhere.
