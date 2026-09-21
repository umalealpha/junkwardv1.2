"""
fnb/statements.py

Pull bank statements from FNB Botswana via the Customer Statement
Execution API and materialise them as `banking.BankStatement` +
`banking.BankStatementLine` rows so the existing reconciliation engine
can match them against open Invoices, Bills, and Payments.

Endpoint: POST /statements/retrieveStatement/v1/
Auth:     OAuth2 client_credentials (handled by FNBClient)
Body:     CustomerStatementRequest { accountId, fromDate, toDate, ... }
Response: CustomerStatement { statementLines: [ { date, amount, narrative, ... } ] }

The mapping from FNB's CustomerStatement schema to our banking models is
deliberately defensive — FNB's schema has several optional fields and the
exact wire format can vary slightly between sandbox and prod. The mapper
in `_persist_statement()` reads the documented fields with a fallback for
each, so a sandbox-vs-prod field name drift doesn't break the import.
"""
from __future__ import annotations

import datetime
import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .client import FNBClient
from .endpoints import STATEMENT_RETRIEVE
from .models import FNBSyncLog

log = logging.getLogger(__name__)

# How far back a BALANCE read looks. Both ends of this number are FIXED by
# measurement against live FNB, not chosen — see fetch_balances.
#
#   TOO NARROW: FNB answers balance 0.00 for any window it finds NO ENTRIES in,
#   so a short window on a quiet account reports a confident zero. The Claims
#   account (…2265) returns nothing at 1, 2, 7 or 14 days and 357 entries at 30.
#
#   TOO WIDE:  🔴 FNB REFUSES ANY WINDOW OVER 30 DAYS WITH HTTP 400. Measured
#   2026-09-20 on …2335 and …2265: 28d ok, 29d ok, 30d ok, 31d 400, 32d 400,
#   34d 400. A 35-day window was deployed for eight minutes and every one of
#   the six accounts failed — the screen had no figures at all.
#
# 30 is the maximum FNB allows and the minimum the quiet account needs. There
# is no margin on either side; _assert_window_is_legal() below makes a future
# change fail loudly instead of silently returning nothing.
BALANCE_WINDOW_DAYS = 30
FNB_MAX_WINDOW_DAYS = 30


def _to_decimal(v) -> Decimal:
    if v in (None, ''):
        return Decimal('0')
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'))
    except Exception:  # noqa: BLE001
        return Decimal('0')


def _to_date(v) -> datetime.date | None:
    if v in (None, ''):
        return None
    if isinstance(v, datetime.date):
        return v
    s = str(v).split('T', 1)[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------

def extract_balances(payload: dict) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """Read (opening, closing) out of a CustomerStatement payload.

    Returns ``None`` for a balance the bank did not send. That is the whole
    point of this function: until 2026-09-20 the caller started both figures
    at ``Decimal('0.00')`` and only overwrote them when an OPBD/CLBD block
    arrived, so "the bank sent no balance" and "the account is empty" were
    the same value. The Claims account (62493282265) reported a confident
    P0.00 every morning while the bank actually held about P256,000.

    A genuine zero still comes back as ``Decimal('0.00')`` — an empty account
    must not become an unknown one either.
    """
    stmt_root = payload.get('statement') or payload

    def _bal_amount(bal: dict) -> Optional[Decimal]:
        raw = bal.get('amountValue')
        if raw in (None, ''):
            raw = bal.get('amount')
        if raw in (None, ''):
            return None
        try:
            # ROUND_HALF_UP: rounding money is a decision, never a language
            # default (house rule). Decimal's default is HALF_EVEN.
            amt = Decimal(str(raw)).quantize(Decimal('0.01'),
                                             rounding=ROUND_HALF_UP)
        except Exception:  # noqa: BLE001
            return None
        cdi = (bal.get('creditDebitIndicator') or '').upper()
        if cdi in ('DEBIT', 'DBIT') and amt > 0:
            amt = -amt
        return amt

    opening = closing = None
    balances = stmt_root.get('balance') or stmt_root.get('balances') or []
    if isinstance(balances, list):
        for b in balances:
            if not isinstance(b, dict):
                continue
            tc = (b.get('typeCode') or b.get('type') or '').upper()
            if tc in ('OPBD', 'PRCD', 'OPENING'):
                opening = _bal_amount(b)
            elif tc in ('CLBD', 'CLAV', 'CLOSING'):
                closing = _bal_amount(b)
    elif isinstance(balances, dict):  # sandbox shape
        opening = _bal_amount({'amountValue': balances.get('opening')})
        closing = _bal_amount({'amountValue': balances.get('closing')})

    # Sandbox-style flat balance fallback. Only consulted when the balance
    # block gave nothing at all — a real 0.00 from CLBD now wins, where the
    # old `== 0` test let a stray flat key override it.
    if opening is None:
        opening = _bal_amount({'amountValue': (
            payload.get('openingBalance') or payload.get('openingBalanceAmount')
        )})
    if closing is None:
        closing = _bal_amount({'amountValue': (
            payload.get('closingBalance') or payload.get('closingBalanceAmount')
        )})

    return opening, closing


def _assert_window_is_legal(days: int) -> None:
    """FNB refuses a window over 30 days with a bare HTTP 400 — no message that
    says why. Fail here, with the reason, rather than at the bank."""
    if days > FNB_MAX_WINDOW_DAYS:
        raise ValueError(
            f'BALANCE_WINDOW_DAYS is {days}; FNB refuses any window over '
            f'{FNB_MAX_WINDOW_DAYS} days with HTTP 400 (measured 2026-09-20: '
            f'30 days ok, 31 days refused). A wider window returns nothing at '
            f'all, not more.')


def fetch_balances(
    bank_account,
    *,
    on_date: Optional[datetime.date] = None,
    user = None,
) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """Ask FNB for ``bank_account``'s balance as at ``on_date``.

    🔴 THE WINDOW MUST SPAN SEVERAL DAYS, AND THE REASON IS NOT OBVIOUS.

    The first version asked for a ONE-DAY window (``fromDate == toDate``) on
    the theory that the balance block comes back regardless of the transaction
    list. It does come back — as ZERO. Measured against live FNB on Sunday
    2026-09-20 for account …2335:

        1 day  (today)      entries=0    OPBD 0.00        CLBD 0.00
        1 day  (yesterday)  entries=70   OPBD 995,103.12  CLBD 1,341,798.13
        2 days              entries=70   OPBD 995,103.12  CLBD 1,341,798.13
        7 days              entries=182  OPBD 631,972.51  CLBD 1,341,798.13

    A day the bank has not opened yet answers 0.00 in a shape indistinguishable
    from a real zero — so the CFO's screen would have shown P0.00 against an
    account holding BWP 1,341,798.13. That is precisely the false zero this
    whole feature exists to kill, reintroduced one layer down.

    Seven days always spans a business day, and CLBD is the closing balance as
    at the END of the window, so it is the current figure either way.

    This still creates NO BankStatement and NO BankStatementLine rows: it does
    not call _persist_statement. That is what keeps three pulls a day from
    multiplying statement rows — the window length was never what did it.

    Writes nothing. Returns (opening, closing), either of which may be None.
    """
    if on_date is None:
        on_date = timezone.localdate()
    _assert_window_is_legal(BALANCE_WINDOW_DAYS)

    client = FNBClient(user=user)
    body = {
        'accountId': bank_account.account_number,
        'fromDate':  (on_date - datetime.timedelta(days=BALANCE_WINDOW_DAYS)).isoformat(),
        'toDate':    on_date.isoformat(),
    }
    resp = client.post(
        STATEMENT_RETRIEVE,
        service        = FNBSyncLog.Service.STATEMENT,
        json_body      = body,
        request_summary= (f'Balance {bank_account.account_number} '
                          f'{on_date.isoformat()}'),
        extra_headers  = {'X-Request-ID': str(uuid.uuid4())},
        # Same 90s window as pull_statement — FNB statement retrieval is
        # consistently slow (20-90s observed), one-day window or not.
        timeout        = 90,
    )
    payload = resp.json or {}

    # 🔴 NO ENTRIES MEANS THE BALANCE IS UNKNOWN, NOT ZERO.
    #
    # FNB answers OPBD 0.00 / CLBD 0.00 for ANY window it finds no transactions
    # in — in a shape indistinguishable from an account that genuinely holds
    # nothing. Measured against live FNB on 2026-09-20, account …2265, which
    # the FNB app showed holding P300,474.31 at the same moment:
    #
    #     1 day   entries=0    OPBD 0.00         CLBD 0.00
    #     2 days  entries=0    OPBD 0.00         CLBD 0.00
    #     7 days  entries=0    OPBD 0.00         CLBD 0.00
    #     14 days entries=0    OPBD 0.00         CLBD 0.00
    #     30 days entries=357  OPBD 1,182,023.36 CLBD 256,229.74
    #
    # The CFO's screen went live reading "Alpha Direct Claims — 0.00, confirmed
    # by the bank" against an account holding P300k, and it was caught only
    # because he sent a screenshot of the FNB app. Widening the window alone
    # does not fix this: it moves the boundary, it does not remove it. A
    # genuinely dormant account will always return no entries, and for that
    # account a zero balance is unknowable from this endpoint.
    #
    # So: no entries, no figure. The screen says "the bank sent no balance"
    # rather than inventing a confident zero.
    statement = payload.get('statement') or {}
    if not (statement.get('entry') or []):
        log.info('FNB balance for %s: no entries in the window, so the 0.00 '
                 'balance block is not trustworthy — reporting unknown',
                 bank_account.account_number)
        return None, None

    return extract_balances(payload)


@transaction.atomic
def pull_statement(
    bank_account,
    *,
    from_date: Optional[datetime.date] = None,
    to_date:   Optional[datetime.date] = None,
    user = None,
):
    """Fetch the FNB statement for `bank_account` over [from_date, to_date]
    and load it into banking.BankStatement(+Line).

    Returns the BankStatement row created (or the existing row if the same
    period was already imported — match on account + (from_date, to_date)).
    """
    from banking.models import BankStatement, BankStatementLine

    if to_date is None:
        to_date = timezone.localdate()
    if from_date is None:
        from_date = to_date - datetime.timedelta(days=31)

    client = FNBClient(user=user)
    # FNB working sample (confirmed by Kabelo Sekoto via Postman screenshot
    # 2026-05-25): exactly three keys. Adding `currency` or
    # `statementType` returns 400 "Bad Request".
    body = {
        'accountId':  bank_account.account_number,
        'fromDate':   from_date.isoformat(),
        'toDate':     to_date.isoformat(),
    }
    resp = client.post(
        STATEMENT_RETRIEVE,
        service        = FNBSyncLog.Service.STATEMENT,
        json_body      = body,
        request_summary= (f'Retrieve statement {bank_account.account_number} '
                          f'{from_date}..{to_date}'),
        extra_headers  = {'X-Request-ID': str(uuid.uuid4())},
        # FNB statement retrieval is consistently slow (>20s observed
        # 2026-05-26). Give it a 90s read window so a busy day's
        # statement does not time out at the default 15s.
        timeout        = 90,
    )
    return _persist_statement(bank_account, resp.json or {}, from_date, to_date)


def _persist_statement(bank_account, payload: dict, from_date, to_date):
    """Translate a CustomerStatement JSON payload into BankStatement+Line.

    Live FNB Botswana response shape (verified 2026-05-25 against
    63001966639):

        {
          "groupHeader": {...},
          "statement": {
            "account": {"accountNumber": "...", "currency": "BWP", ...},
            "balance": [
              {"typeCode": "OPBD", "amountValue": ..., "creditDebitIndicator": "Credit"},
              {"typeCode": "CLBD", "amountValue": ..., "creditDebitIndicator": "Credit"}
            ],
            "entry": [
              {"amountValue": 53.6, "creditDebitIndicator": "Debit",
               "bookingDateTime": "2026-06-01", "valueDate": "2026-06-01",
               "servicerReference": "#MONTHLY ACCOUNT FEE ",
               "bankTransactionCode": {"domainCode": "ACMT", ...},
               "transactionDetails": {"amountValue": 53.6, ...}}
            ]
          }
        }

    Sandbox-style flat keys (`statementLines` / `openingBalance`) are kept
    as fallbacks.
    """
    from banking.models import BankStatement, BankStatementLine

    stmt_root = payload.get('statement') or payload

    # Entries: live key is `entry` under `statement`. Older docs / sandbox
    # used `statementLines` / `transactions` / `entries`.
    lines = (
        stmt_root.get('entry')
        or stmt_root.get('entries')
        or stmt_root.get('statementLines')
        or stmt_root.get('transactions')
        or []
    )

    # Balance array: OPBD (opening booked) and CLBD (closing booked), read by
    # extract_balances() above. Either may be None — "the bank sent no
    # balance" is recorded as unknown, never as 0.00.
    opening_balance, closing_balance = extract_balances(payload)

    # BankStatement model only has a single `statement_date`; use to_date
    # (period end) as that date. Idempotency: match on
    # (bank_account, statement_date) so re-pulling the same range overwrites.
    file_name = f'FNB API {from_date.isoformat()}..{to_date.isoformat()}'
    statement = BankStatement.objects.filter(
        bank_account=bank_account,
        statement_date=to_date,
        file_name=file_name,
    ).first()
    if statement:
        statement.opening_balance = opening_balance
        statement.closing_balance = closing_balance
        statement.save(update_fields=['opening_balance', 'closing_balance'])
    else:
        statement = BankStatement.objects.create(
            bank_account=bank_account,
            statement_date=to_date,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            file_name=file_name,
        )

    # Wipe old lines for this statement and reinsert. Re-import is idempotent
    # on the (account, statement_date, file_name) tuple, not line content.
    BankStatementLine.objects.filter(statement=statement).delete()

    # No-double-import guard (2026-09-12): a line already present under a
    # DIFFERENT statement (e.g. an overlapping-date-range re-pull) is skipped,
    # not recreated, so it is never double-counted for reconciliation. The
    # lines just deleted above are gone from this set already.
    #
    # Keyed on an OCCURRENCE count, not line position — see banking/models.py
    # compute_line_dedupe_key() for why line_number/position cannot be used:
    # an overlapping re-pull puts the same transaction at a different
    # position, so a position-keyed guard would miss the exact case it exists
    # to catch.
    from banking.models import compute_line_dedupe_key, line_identity
    existing_keys = set(BankStatementLine.objects.filter(
        statement__bank_account=bank_account,
    ).values_list('dedupe_key', flat=True))
    occurrence_counts: dict = {}
    duplicate_lines = 0

    created = 0
    for ln in lines:
        # FNB Botswana response (confirmed 2026-05-25) nests amount,
        # currency, CDI, reference and counterparty under
        # `transactionDetails`. Top-level fields exist for booking
        # dates and bankTransactionCode classification. Keep flat
        # fallbacks for sandbox parity.
        td = ln.get('transactionDetails') or {}
        btc = ln.get('bankTransactionCode') or {}

        amt = _to_decimal(
            td.get('amountValue')
            or ln.get('amount')
            or ln.get('value')
        )
        cr_dr_raw = (
            td.get('creditDebitIndicator')
            or ln.get('creditDebitIndicator')
            or ln.get('cdi')
            or ''
        )
        cr_dr = cr_dr_raw.upper()
        # FNB BW uses "Debit"/"Credit"; ISO 20022 spec uses "DBIT"/"CRDT".
        # Treat both forms; outflow = negative.
        is_debit  = cr_dr in ('DEBIT',  'DBIT')
        is_credit = cr_dr in ('CREDIT', 'CRDT')
        if is_debit and amt > 0:
            amt = -amt
        elif is_credit and amt < 0:
            amt = -amt

        # Description: prefer `servicerReference` (FNB BW narrative),
        # then build "DOMAIN/FAMILY/SUBFAMILY" from bankTransactionCode,
        # then fall back to sandbox-style fields.
        desc_parts = []
        sr = ln.get('servicerReference')
        if sr:
            desc_parts.append(str(sr).strip())
        btc_str = '/'.join(
            x for x in (
                btc.get('domainCode'),
                btc.get('domainFamilyCode'),
                btc.get('domainSubFamilyCode'),
            ) if x
        )
        if btc_str:
            desc_parts.append(btc_str)
        counterparty = (
            td.get('relatedPartyDebtorName')
            or td.get('relatedPartyDebitorName')   # FNB spec typo
            or td.get('relatedPartyCreditorName')
        )
        if counterparty:
            desc_parts.append(str(counterparty).strip())
        description = ' | '.join(desc_parts) or (
            ln.get('description')
            or ln.get('narrative')
            or ln.get('remittanceInformation')
            or ''
        )

        reference = (
            td.get('referenceEndToEndId')
            or ln.get('reference')
            or ln.get('endToEndId')
            or ''
        )

        txn_date = _to_date(
            ln.get('bookingDateTime')
            or ln.get('bookingDate')
            or ln.get('valueDate')
            or ln.get('date')
        ) or to_date
        desc_trunc = str(description)[:500]
        ref_trunc  = str(reference)[:200]

        identity = line_identity(bank_account.id, txn_date, amt, desc_trunc, ref_trunc)
        occurrence = occurrence_counts.get(identity, 0)
        occurrence_counts[identity] = occurrence + 1
        key = compute_line_dedupe_key(
            bank_account.id, txn_date, amt, desc_trunc, ref_trunc,
            occurrence=occurrence)
        if key in existing_keys:
            duplicate_lines += 1
            continue
        existing_keys.add(key)

        # Counted here, not before the skip above: `created` (and
        # `line_count` below) must equal the rows actually STORED, not
        # entries merely seen — a duplicate that was skipped is not a line.
        created += 1
        BankStatementLine.objects.create(
            statement        = statement,
            line_number      = created,
            transaction_date = txn_date,
            amount           = amt,
            description      = desc_trunc,
            reference        = ref_trunc,
            raw_data         = ln,
            dedupe_key       = key,
        )

    statement.line_count = created
    statement.save(update_fields=['line_count'])

    if duplicate_lines:
        log.info('FNB statement %s %s..%s: skipped %d duplicate line(s) '
                 'already present.', bank_account.account_number, from_date,
                 to_date, duplicate_lines)

    log.info('FNB statement loaded: %s %s..%s (%d lines, closing=%s)',
             bank_account.account_number, from_date, to_date,
             created, closing_balance)
    return statement
