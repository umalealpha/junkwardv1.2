"""reinsurance/services.py — JE posting + state transitions."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ledger.models import Account, JournalEntry, JournalEntryLine

from .models import Cession, ReinsuranceRecovery


ZERO = Decimal('0.00')

# GL account codes used by reinsurance postings
ACCT_CEDED_PREMIUM_PAYABLE   = '2120'
ACCT_REINSURANCE_RECEIVABLE  = '1230'
ACCT_REINSURANCE_CEDED       = '4200'
ACCT_REINSURANCE_RECOVERY    = '5200'


def _account(code):
    try:
        return Account.objects.get(code=code)
    except Account.DoesNotExist as exc:
        raise ValidationError(
            f'Required GL account {code} not found. Run setup_chart_of_accounts.'
        ) from exc


# ---------------------------------------------------------------------------
# Cession
# ---------------------------------------------------------------------------

@transaction.atomic
def post_cession(cession: Cession, user: User) -> Cession:
    """DRAFT → POSTED. Books DR 4200 / CR 2120 for the ceded premium.

    If commission is non-zero, that's deducted from the payable side via a
    second CR to 4400 (commission income). Total DR = total CR.
    """
    if cession.status != Cession.Status.DRAFT:
        raise ValidationError(
            f'Only draft cessions can be posted (was {cession.status}).'
        )
    if cession.ceded_premium <= ZERO:
        raise ValidationError({'ceded_premium': 'Must be positive.'})
    if cession.commission_amount < ZERO:
        raise ValidationError({'commission_amount': 'Cannot be negative.'})
    if cession.commission_amount > cession.ceded_premium:
        raise ValidationError({
            'commission_amount': 'Commission cannot exceed ceded premium.',
        })

    ceded_acct = _account(ACCT_REINSURANCE_CEDED)
    payable_acct = _account(ACCT_CEDED_PREMIUM_PAYABLE)

    je = JournalEntry.objects.create(
        entry_date=cession.cession_date,
        description=(
            f'Cession {cession.cession_number} — '
            f'{cession.treaty.treaty_number} — {cession.treaty.reinsurer.short_code}'
        ),
        source_type='reinsurance_cession',
        source_id=cession.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.0'),
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    # DR ceded premium expense (gross of commission)
    JournalEntryLine.objects.create(
        journal_entry=je, account=ceded_acct,
        description=f'Ceded premium — {cession.treaty.reinsurer.short_code}',
        debit_amount=cession.ceded_premium, credit_amount=ZERO,
        debit_bwp=cession.ceded_premium, credit_bwp=ZERO,
    )
    net_payable = cession.ceded_premium - cession.commission_amount
    if net_payable > ZERO:
        JournalEntryLine.objects.create(
            journal_entry=je, account=payable_acct,
            description=f'Net payable to {cession.treaty.reinsurer.short_code}',
            debit_amount=ZERO, credit_amount=net_payable,
            debit_bwp=ZERO, credit_bwp=net_payable,
        )
    if cession.commission_amount > ZERO:
        commission_acct = _account('4400')  # Commission income
        JournalEntryLine.objects.create(
            journal_entry=je, account=commission_acct,
            description=f'Reinsurance commission earned',
            debit_amount=ZERO, credit_amount=cession.commission_amount,
            debit_bwp=ZERO, credit_bwp=cession.commission_amount,
        )
    je.post(user=user, _allow_direct=True)

    cession.status = Cession.Status.POSTED
    cession.journal_entry = je
    cession.posted_by = user
    cession.posted_at = timezone.now()
    cession.save(audit_user=user, audit_description=f'Posted {cession.cession_number}')
    return cession


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

@transaction.atomic
def post_recovery(recovery: ReinsuranceRecovery, user: User) -> ReinsuranceRecovery:
    """DRAFT → POSTED. Books DR 1230 / CR 5200 for the ceded recovery."""
    if recovery.status != ReinsuranceRecovery.Status.DRAFT:
        raise ValidationError(
            f'Only draft recoveries can be posted (was {recovery.status}).'
        )
    if recovery.ceded_recovery <= ZERO:
        raise ValidationError({'ceded_recovery': 'Must be positive.'})
    if not (recovery.claim_reference or '').strip():
        raise ValidationError({'claim_reference': 'Claim reference is required.'})

    receivable_acct = _account(ACCT_REINSURANCE_RECEIVABLE)
    recovery_acct = _account(ACCT_REINSURANCE_RECOVERY)

    je = JournalEntry.objects.create(
        entry_date=recovery.recovery_date,
        description=(
            f'Recovery {recovery.recovery_number} — '
            f'claim {recovery.claim_reference} — {recovery.treaty.reinsurer.short_code}'
        ),
        source_type='reinsurance_recovery',
        source_id=recovery.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.0'),
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry=je, account=receivable_acct,
        description=f'Reinsurance receivable from {recovery.treaty.reinsurer.short_code}',
        debit_amount=recovery.ceded_recovery, credit_amount=ZERO,
        debit_bwp=recovery.ceded_recovery, credit_bwp=ZERO,
    )
    JournalEntryLine.objects.create(
        journal_entry=je, account=recovery_acct,
        description=f'Recovery on claim {recovery.claim_reference}',
        debit_amount=ZERO, credit_amount=recovery.ceded_recovery,
        debit_bwp=ZERO, credit_bwp=recovery.ceded_recovery,
    )
    je.post(user=user, _allow_direct=True)

    recovery.status = ReinsuranceRecovery.Status.POSTED
    recovery.journal_entry = je
    recovery.posted_by = user
    recovery.posted_at = timezone.now()
    recovery.save(audit_user=user, audit_description=f'Posted {recovery.recovery_number}')
    return recovery
