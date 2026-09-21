"""investments/services.py — JE posting for investment transactions."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ledger.models import Account, JournalEntry, JournalEntryLine

from .models import InvestmentTransaction


ZERO = Decimal('0.00')

INVESTMENT_INCOME_ACCOUNT = '4500'


def _income_account():
    try:
        return Account.objects.get(code=INVESTMENT_INCOME_ACCOUNT)
    except Account.DoesNotExist as exc:
        raise ValidationError(
            f'GL account {INVESTMENT_INCOME_ACCOUNT} (Investment income) '
            f'missing. Run setup_chart_of_accounts.'
        ) from exc


def _je_header(tx, user, description):
    return JournalEntry.objects.create(
        entry_date=tx.transaction_date,
        description=description,
        source_type='investment_transaction',
        source_id=tx.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.0'),
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )


def _line(je, account, dr, cr, description):
    JournalEntryLine.objects.create(
        journal_entry=je, account=account, description=description,
        debit_amount=dr, credit_amount=cr,
        debit_bwp=dr, credit_bwp=cr,
    )


@transaction.atomic
def post_transaction(tx: InvestmentTransaction, user: User) -> InvestmentTransaction:
    """DRAFT → POSTED. Routes by transaction_type to the right JE pattern."""
    if tx.status != InvestmentTransaction.Status.DRAFT:
        raise ValidationError(
            f'Only draft transactions can be posted (was {tx.status}).'
        )
    tx.full_clean(exclude=['transaction_number'])

    inv = tx.investment
    inv_acct = inv.investment_account
    cash = tx.cash_account
    amt = tx.amount
    abs_amt = abs(amt)

    desc = (
        f'{tx.transaction_number} — {tx.get_transaction_type_display()} — '
        f'{inv.investment_number} {inv.name}'
    )
    je = _je_header(tx, user, desc)

    if tx.transaction_type == InvestmentTransaction.TransactionType.PURCHASE:
        # DR investment / CR bank
        _line(je, inv_acct, abs_amt, ZERO, f'Purchase of {inv.name}')
        _line(je, cash, ZERO, abs_amt, 'Cash settlement')

    elif tx.transaction_type == InvestmentTransaction.TransactionType.SALE:
        # DR bank / CR investment (at carrying value, so use abs_amt for now —
        # realised gain/loss accounting is a v2 enhancement requiring a
        # carrying-value field on the transaction).
        _line(je, cash, abs_amt, ZERO, f'Proceeds — sale of {inv.name}')
        _line(je, inv_acct, ZERO, abs_amt, 'Carrying value released')

    elif tx.transaction_type == InvestmentTransaction.TransactionType.COUPON:
        # DR bank / CR 4500 Investment income
        income = _income_account()
        _line(je, cash, abs_amt, ZERO, f'Coupon / interest received — {inv.name}')
        _line(je, income, ZERO, abs_amt, 'Investment income')

    elif tx.transaction_type == InvestmentTransaction.TransactionType.FAIR_VALUE:
        income = _income_account()
        if amt > ZERO:
            # Unrealised gain: DR investment / CR 4500
            _line(je, inv_acct, amt, ZERO, f'FV uplift on {inv.name}')
            _line(je, income, ZERO, amt, 'FV uplift through P&L')
            inv.current_fair_value = (inv.current_fair_value or ZERO) + amt
        else:
            # Unrealised loss: DR 4500 / CR investment
            _line(je, income, abs_amt, ZERO, 'FV write-down through P&L')
            _line(je, inv_acct, ZERO, abs_amt, f'FV write-down on {inv.name}')
            inv.current_fair_value = (inv.current_fair_value or ZERO) - abs_amt

    elif tx.transaction_type == InvestmentTransaction.TransactionType.MATURITY:
        # DR bank / CR investment — full redemption
        _line(je, cash, abs_amt, ZERO, f'Redemption proceeds — {inv.name}')
        _line(je, inv_acct, ZERO, abs_amt, 'Investment matured')
        inv.status = inv.Status.MATURED

    else:
        raise ValidationError(f'Unsupported transaction type: {tx.transaction_type}')

    je.post(user=user, _allow_direct=True)

    tx.status = InvestmentTransaction.Status.POSTED
    tx.journal_entry = je
    tx.posted_by = user
    tx.posted_at = timezone.now()
    tx.save(audit_user=user, audit_description=f'Posted {tx.transaction_number}')
    inv.save(audit_user=user)
    return tx
