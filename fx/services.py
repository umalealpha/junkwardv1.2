"""
fx/services.py

Period-end foreign-currency revaluation engine.

For each balance-sheet account whose currency is NOT BWP, compute the
unrealised gain or loss between:

    book_bwp      = sum(debit_bwp) - sum(credit_bwp) on that account
    foreign_bal   = sum(debit_amount) - sum(credit_amount) on that account
    revalued_bwp  = foreign_bal * closing_rate
    gain_or_loss  = revalued_bwp - book_bwp     (sign convention for assets)

Sign for liabilities is reversed: a stronger BWP makes a foreign liability
SMALLER in BWP terms, which is a GAIN.

The revaluation JE is posted with one line per account plus a balancing
gain/loss line on 6950.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from core.models import AuditLog, Currency, ExchangeRate
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine

from .models import FXRevaluation, FXRevaluationLine, FxRevaluationRun


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')

FX_GAIN_LOSS_ACCOUNT_CODE = '6950'   # Foreign exchange gain/loss

# Account types that get revalued (balance-sheet accounts only)
BS_ACCOUNT_TYPES = {'asset', 'liability', 'equity'}


def _get_closing_rate(currency, as_of_date) -> Optional[Decimal]:
    """Latest APPROVED ExchangeRate(currency -> BWP) on or before *as_of_date*.

    FX-001 (CFO/Oprah directive 2026-05-28): rates must be Finance-Manager-
    approved (Bank of Botswana mid-rates) before any revaluation can use
    them. Unapproved rates are invisible here.
    """
    rate = (
        ExchangeRate.objects
        .filter(from_currency=currency, to_currency_id='BWP',
                effective_date__lte=as_of_date,
                approved_by__isnull=False, approved_at__isnull=False)
        .order_by('-effective_date')
        .first()
    )
    return rate.rate if rate else None


def _account_balance(account: Account, as_of_date) -> tuple[Decimal, Decimal]:
    """
    Return (foreign_balance, bwp_book_balance) on *account* up to *as_of_date*.
    Sum is debit minus credit (asset/expense convention).

    Only POSTED journal-entry lines are considered.
    """
    qs = JournalEntryLine.objects.filter(
        account=account,
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=as_of_date,
    )
    aggregates = qs.aggregate(
        d_amt=Sum('debit_amount'),  c_amt=Sum('credit_amount'),
        d_bwp=Sum('debit_bwp'),     c_bwp=Sum('credit_bwp'),
    )
    foreign = (aggregates['d_amt'] or ZERO) - (aggregates['c_amt'] or ZERO)
    bwp     = (aggregates['d_bwp'] or ZERO) - (aggregates['c_bwp'] or ZERO)
    return foreign, bwp


@transaction.atomic
def run_fx_revaluation(period: FiscalPeriod, user: User,
                       *, company=None, dry_run: bool = False) -> FXRevaluation:
    """
    Compute and post a period-end FX revaluation for *period*.

    If *dry_run* is True, builds the FXRevaluation in DRAFT status with all
    lines but does NOT post the JE. Useful for review before commitment.

    Idempotency: an existing POSTED FXRevaluation for (period, company) blocks
    a fresh run. Reverse the existing one first.
    """
    if FXRevaluation.objects.filter(
        period=period, company=company,
        status=FXRevaluation.Status.POSTED,
    ).exists():
        raise ValidationError(
            f"An FX revaluation has already been posted for {period.period_name}. "
            "Reverse the existing run first."
        )

    as_of = period.end_date

    # Resolve gain/loss account
    try:
        gain_loss_acct = Account.objects.get(code=FX_GAIN_LOSS_ACCOUNT_CODE)
    except Account.DoesNotExist:
        raise ValidationError(
            f"Account {FX_GAIN_LOSS_ACCOUNT_CODE} (FX gain/loss) is missing. "
            "Run setup_chart_of_accounts."
        )

    # Find all foreign-currency balance-sheet accounts
    foreign_accounts = (
        Account.objects
        .filter(account_type__in=BS_ACCOUNT_TYPES, is_active=True)
        .exclude(currency_code_id='BWP')
        .select_related('currency_code')
    )

    # FX-001 gate: every non-BWP currency on a foreign-currency BS account
    # must have an approved BoB mid-rate at or before period.end_date. This
    # blocks the run entirely (no draft created) so the caller sees one
    # clear error listing the missing currencies, not a silently-empty run.
    currency_codes = sorted({
        a.currency_code_id for a in foreign_accounts if a.currency_code_id != 'BWP'
    })
    if currency_codes:
        assert_approved_rates_exist(currency_codes, as_of)

    reval = FXRevaluation.objects.create(
        period=period,
        company=company,
        run_date=timezone.localdate(),
        status=FXRevaluation.Status.DRAFT,
        created_by=user,
    )

    total_gain = ZERO
    total_loss = ZERO
    je_lines_to_create = []

    for account in foreign_accounts:
        currency = account.currency_code
        if currency.code == 'BWP':
            continue

        closing_rate = _get_closing_rate(currency, as_of)
        if closing_rate is None:
            # Skip silently — no rate, no revaluation. Could log a warning.
            continue

        foreign_bal, book_bwp = _account_balance(account, as_of)
        if foreign_bal == ZERO:
            continue

        revalued_bwp = (foreign_bal * closing_rate).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP,
        )
        reval_amount = revalued_bwp - book_bwp

        # Sign convention for liabilities: a higher BWP value of a liability is
        # a LOSS (you owe more). For assets, higher BWP value is a GAIN.
        # In the GL, both follow the same DR/CR mechanic on the account itself
        # (DR/CR vs the balancing 6950 line) — `reval_amount` carries the sign
        # for the account; the 6950 line takes the opposite.

        FXRevaluationLine.objects.create(
            revaluation=reval, account=account, currency_code=currency,
            balance_foreign=foreign_bal,
            closing_rate=closing_rate,
            book_bwp=book_bwp,
            revalued_bwp=revalued_bwp,
            revaluation_amount=reval_amount,
        )

        if reval_amount > ZERO:
            total_gain += reval_amount
        else:
            total_loss += abs(reval_amount)

        je_lines_to_create.append((account, reval_amount))

    reval.total_gain_bwp = total_gain
    reval.total_loss_bwp = total_loss
    reval.net_bwp        = total_gain - total_loss
    reval.save(audit_user=user, audit_description=f"FX revaluation lines for {period.period_name}")

    if dry_run:
        return reval

    if not je_lines_to_create:
        # Nothing to revalue
        return reval

    # Build the JE
    je = JournalEntry.objects.create(
        entry_date    = as_of,
        description   = f"FX revaluation — {period.period_name}",
        source_type   = 'fx_revaluation',
        source_id     = reval.pk,
        journal_type  = JournalEntry.JournalType.GENERAL,
        currency_code_id = 'BWP',
        exchange_rate = Decimal('1.00000000'),
        company       = company,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
        is_related_party = False,
    )

    net_for_gain_loss = ZERO
    for account, amount in je_lines_to_create:
        if amount == ZERO:
            continue
        if amount > ZERO:
            # Account value goes up in BWP -> debit the account (asset/expense
            # convention). For liabilities this is the same DB entry — Django
            # doesn't auto-flip; the sign of the asset-side gain is what we
            # use to drive 6950.
            JournalEntryLine.objects.create(
                journal_entry=je, account=account,
                description=f"FX reval {account.code}",
                debit_amount=amount,  credit_amount=ZERO,
                debit_bwp=amount,     credit_bwp=ZERO,
            )
            net_for_gain_loss -= amount   # 6950 takes the opposite sign
        else:
            credit = abs(amount)
            JournalEntryLine.objects.create(
                journal_entry=je, account=account,
                description=f"FX reval {account.code}",
                debit_amount=ZERO,  credit_amount=credit,
                debit_bwp=ZERO,     credit_bwp=credit,
            )
            net_for_gain_loss += credit

    # 6950 balancing line
    if net_for_gain_loss > ZERO:
        JournalEntryLine.objects.create(
            journal_entry=je, account=gain_loss_acct,
            description=f"Net FX loss for {period.period_name}",
            debit_amount=net_for_gain_loss,  credit_amount=ZERO,
            debit_bwp=net_for_gain_loss,     credit_bwp=ZERO,
        )
    elif net_for_gain_loss < ZERO:
        JournalEntryLine.objects.create(
            journal_entry=je, account=gain_loss_acct,
            description=f"Net FX gain for {period.period_name}",
            debit_amount=ZERO,            credit_amount=abs(net_for_gain_loss),
            debit_bwp=ZERO,               credit_bwp=abs(net_for_gain_loss),
        )

    je.post(user=user, _allow_direct=True)

    reval.journal_entry = je
    reval.status        = FXRevaluation.Status.POSTED
    reval.save(audit_user=user, audit_description=f"Posted FX revaluation {period.period_name}")

    AuditLog.objects.create(
        table_name='FXRevaluation',
        record_id=str(reval.pk),
        action=AuditLog.Action.POST,
        new_values={
            'period':         period.period_name,
            'gain':           str(total_gain),
            'loss':           str(total_loss),
            'journal_entry':  str(je.pk),
        },
        user=user,
        description=f"Posted FX revaluation for {period.period_name}",
    )

    return reval


# ---------------------------------------------------------------------------
# revalue_period — Banking + Treasury upgrade (2026-05-24)
#
# Lightweight period-end revaluation focused on:
#   * open non-BWP Invoices (status in POSTED/PARTIALLY_PAID/OVERDUE)
#   * non-BWP BankAccount net GL balances
#
# Posts ONE JE per company at period_end_date using:
#   * 7100  Unrealised FX gain/loss      (P&L)
#   * 2900  FX revaluation suspense      (BS counter-leg)
#
# Idempotent on (company_id, period_end). Re-running for the same key skips
# unless the prior FxRevaluationRun has been marked is_reversed=True.
# ---------------------------------------------------------------------------

import logging

logger = logging.getLogger(__name__)

UNREALISED_FX_PNL_CODE       = '7100'  # P&L  Unrealised FX gain/loss
FX_REVAL_SUSPENSE_BS_CODE    = '2900'  # BS   FX revaluation suspense

OPEN_INVOICE_STATUSES = ('posted', 'partially_paid', 'overdue')


def _closing_rate(currency_code: str, period_end_date) -> Optional[Decimal]:
    """Latest APPROVED Bank-of-Botswana mid-rate for *currency_code* on or
    before *period_end_date* (FX-001, CFO/Oprah directive 2026-05-28).
    Unapproved rates are invisible to revaluation."""
    rate = (
        ExchangeRate.objects
        .filter(from_currency_id=currency_code,
                to_currency_id='BWP',
                effective_date__lte=period_end_date,
                approved_by__isnull=False,
                approved_at__isnull=False)
        .order_by('-effective_date')
        .first()
    )
    return rate.rate if rate else None


def closing_rate_with_meta(currency_code: str, period_end_date):
    """Same as _closing_rate but returns the full ExchangeRate row (for UI
    display: source, rate, approved_by, approved_at). Returns None if no
    approved rate exists."""
    return (
        ExchangeRate.objects
        .select_related('approved_by', 'loaded_by')
        .filter(from_currency_id=currency_code,
                to_currency_id='BWP',
                effective_date__lte=period_end_date,
                approved_by__isnull=False,
                approved_at__isnull=False)
        .order_by('-effective_date')
        .first()
    )


def assert_approved_rates_exist(currency_codes, period_end_date):
    """Raise DjangoValidationError listing any currencies missing an
    approved BoB mid-rate at period_end_date. Use before running a
    revaluation."""
    from django.core.exceptions import ValidationError as _VErr
    missing = []
    for cc in currency_codes:
        if cc == 'BWP':
            continue
        if _closing_rate(cc, period_end_date) is None:
            missing.append(cc)
    if missing:
        raise _VErr(
            f"Cannot revalue: no approved BoB mid-rate found for "
            f"{', '.join(missing)} on or before {period_end_date}. "
            f"Load + approve the rate at /settings/fx-rates first."
        )


def _bank_account_balance(bank_gl_account, period_end_date) -> tuple[Decimal, Decimal]:
    """
    Return (foreign_balance, bwp_book_balance) for a bank GL account up to
    *period_end_date*, summed across POSTED JE lines.
    """
    qs = JournalEntryLine.objects.filter(
        account=bank_gl_account,
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=period_end_date,
    )
    agg = qs.aggregate(
        d_amt=Sum('debit_amount'), c_amt=Sum('credit_amount'),
        d_bwp=Sum('debit_bwp'),    c_bwp=Sum('credit_bwp'),
    )
    foreign = (agg['d_amt'] or ZERO) - (agg['c_amt'] or ZERO)
    bwp     = (agg['d_bwp'] or ZERO) - (agg['c_bwp'] or ZERO)
    return foreign, bwp


@transaction.atomic
def revalue_period(period_end_date, company_id, *, user: Optional[User] = None
                   ) -> FxRevaluationRun:
    """
    Run the open-invoice + foreign-bank-account revaluation for one company
    at *period_end_date*. Returns the created FxRevaluationRun (or the
    existing un-reversed one if idempotency kicks in).

    Sign convention:
      delta_bwp = outstanding_native * closing_rate - outstanding_bwp

      delta > 0  -> book GAIN  (Cr 7100, Dr 2900)
      delta < 0  -> book LOSS  (Dr 7100, Cr 2900)
    """
    from core.models import Company
    from billing.models import Invoice
    from banking.models import BankAccount

    # ---- Idempotency -------------------------------------------------------
    existing = (
        FxRevaluationRun.objects
        .filter(company_id=company_id, period_end=period_end_date,
                is_reversed=False)
        .first()
    )
    if existing is not None:
        logger.info(
            "revalue_period: skip — un-reversed run already exists "
            "(company=%s, period_end=%s, run=%s)",
            company_id, period_end_date, existing.pk,
        )
        return existing

    # ---- Required accounts -------------------------------------------------
    try:
        pnl_acct = Account.objects.get(code=UNREALISED_FX_PNL_CODE)
    except Account.DoesNotExist:
        logger.warning(
            "revalue_period: missing CoA account %s (Unrealised FX gain/loss). "
            "Skipping run for company=%s, period_end=%s.",
            UNREALISED_FX_PNL_CODE, company_id, period_end_date,
        )
        return FxRevaluationRun.objects.create(
            company_id=company_id, period_end=period_end_date,
            total_delta_bwp=ZERO,
        )
    try:
        bs_acct = Account.objects.get(code=FX_REVAL_SUSPENSE_BS_CODE)
    except Account.DoesNotExist:
        logger.warning(
            "revalue_period: missing CoA account %s (FX revaluation suspense). "
            "Skipping run for company=%s, period_end=%s.",
            FX_REVAL_SUSPENSE_BS_CODE, company_id, period_end_date,
        )
        return FxRevaluationRun.objects.create(
            company_id=company_id, period_end=period_end_date,
            total_delta_bwp=ZERO,
        )

    company = Company.objects.get(pk=company_id)

    # ---- Bucket: (currency, counter_account_code) -> delta_bwp -------------
    # The prompt asks for one (currency, account_code) bucket on the
    # *counter* leg. Bank-side deltas counter to 2900 by currency; invoice-
    # side deltas counter to 2900 by currency. We keep the buckets to surface
    # one line per currency on the BS suspense side.
    buckets: dict[str, Decimal] = {}

    # ---- Open invoices in non-BWP -----------------------------------------
    open_invoices = (
        Invoice.objects
        .filter(company_id=company_id,
                status__in=OPEN_INVOICE_STATUSES)
        .exclude(currency_code_id='BWP')
        .select_related('currency_code')
    )
    for inv in open_invoices:
        ccy = inv.currency_code_id
        closing = _closing_rate(ccy, period_end_date)
        if closing is None:
            logger.warning(
                "revalue_period: no closing rate for %s on/before %s "
                "(invoice %s). Skipping.", ccy, period_end_date, inv.invoice_number,
            )
            continue
        outstanding_native = inv.balance_due or ZERO
        if outstanding_native == ZERO:
            continue
        # Native-side BWP book value = remaining native * the rate at which
        # the invoice was last posted. balance_due is native; book BWP for
        # the open balance = outstanding_native * inv.exchange_rate.
        outstanding_bwp = (outstanding_native * (inv.exchange_rate or Decimal('1.0'))
                           ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        revalued_bwp = (outstanding_native * closing
                        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        delta = revalued_bwp - outstanding_bwp
        if abs(delta) > TWO_PLACES.scaleb(0):  # > 0.01
            buckets[ccy] = buckets.get(ccy, ZERO) + delta

    # ---- Foreign bank accounts --------------------------------------------
    foreign_banks = (
        BankAccount.objects
        .filter(is_active=True,
                gl_account__owner_company_id=company_id)
        .exclude(currency_code_id='BWP')
        .select_related('gl_account', 'currency_code')
    )
    for ba in foreign_banks:
        ccy = ba.currency_code_id
        closing = _closing_rate(ccy, period_end_date)
        if closing is None:
            logger.warning(
                "revalue_period: no closing rate for %s on/before %s "
                "(bank %s). Skipping.", ccy, period_end_date, ba.account_name,
            )
            continue
        foreign_bal, book_bwp = _bank_account_balance(ba.gl_account, period_end_date)
        if foreign_bal == ZERO:
            continue
        revalued_bwp = (foreign_bal * closing
                        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        delta = revalued_bwp - book_bwp
        if abs(delta) > TWO_PLACES.scaleb(0):
            buckets[ccy] = buckets.get(ccy, ZERO) + delta

    # Strip zero/near-zero buckets
    buckets = {
        ccy: amt.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        for ccy, amt in buckets.items()
        if abs(amt) >= Decimal('0.01')
    }

    total_delta = sum(buckets.values(), ZERO).quantize(TWO_PLACES)

    if not buckets:
        # Nothing to post — still record a run for traceability.
        run = FxRevaluationRun.objects.create(
            company=company, period_end=period_end_date,
            total_delta_bwp=ZERO,
        )
        return run

    # ---- Build ONE JE per company -----------------------------------------
    je = JournalEntry.objects.create(
        entry_date       = period_end_date,
        description      = f"FX revaluation ({company.code}) {period_end_date}",
        source_type      = 'fx_revaluation_run',
        journal_type     = JournalEntry.JournalType.GENERAL,
        currency_code_id = 'BWP',
        exchange_rate    = Decimal('1.00000000'),
        company          = company,
        created_by       = user if user is not None else _system_user(),
        status           = JournalEntry.Status.DRAFT,
        is_related_party = False,
    )

    # Per the spec: opposite leg per currency to 2900. 7100 (P&L) carries the
    # net gain/loss across all currencies on a single line.
    for ccy, delta in buckets.items():
        if delta > ZERO:
            # Gain to entity: Dr 2900 (BS up), Cr 7100 booked on aggregate below.
            JournalEntryLine.objects.create(
                journal_entry=je, account=bs_acct,
                description=f"FX reval suspense {ccy}",
                debit_amount=delta,  credit_amount=ZERO,
                debit_bwp=delta,     credit_bwp=ZERO,
            )
        else:
            # Loss to entity: Cr 2900 (BS down)
            credit = abs(delta)
            JournalEntryLine.objects.create(
                journal_entry=je, account=bs_acct,
                description=f"FX reval suspense {ccy}",
                debit_amount=ZERO,  credit_amount=credit,
                debit_bwp=ZERO,     credit_bwp=credit,
            )

    # Aggregate 7100 counter line — net of all currency buckets.
    if total_delta > ZERO:
        # Net gain across currencies -> Cr 7100
        JournalEntryLine.objects.create(
            journal_entry=je, account=pnl_acct,
            description=f"Net unrealised FX gain {period_end_date}",
            debit_amount=ZERO,           credit_amount=total_delta,
            debit_bwp=ZERO,              credit_bwp=total_delta,
        )
    elif total_delta < ZERO:
        # Net loss -> Dr 7100
        JournalEntryLine.objects.create(
            journal_entry=je, account=pnl_acct,
            description=f"Net unrealised FX loss {period_end_date}",
            debit_amount=abs(total_delta),  credit_amount=ZERO,
            debit_bwp=abs(total_delta),     credit_bwp=ZERO,
        )
    else:
        # Per-currency deltas cancel out exactly — need a zero balancing
        # leg on 7100 to keep two-line minimum; skip if we already have
        # two BS lines that net to zero.
        pass

    # Post the JE
    je.post(user=user if user is not None else _system_user(), _allow_direct=True)

    run = FxRevaluationRun.objects.create(
        company=company,
        period_end=period_end_date,
        journal_entry=je,
        total_delta_bwp=total_delta,
    )
    return run


def _system_user() -> User:
    """
    Return the audit-purposes 'system' user, creating it if missing. Used
    when revalue_period is invoked without an explicit *user* (e.g. cron).
    """
    u, _ = User.objects.get_or_create(
        username='system',
        defaults={'first_name': 'System', 'last_name': 'Automation',
                  'is_active': False},
    )
    return u
