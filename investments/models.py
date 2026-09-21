"""
investments/models.py

Investment register — v1 backbone.

Captures the company's invested-asset portfolio with IFRS 9 classification
(FVTPL / FVOCI / amortised cost), and posts JEs for every transaction
(purchase, sale, coupon receipt, fair-value adjustment).

GL accounts:
  • investment_account   FK on each Investment (chosen by user; typically
                         a balance-sheet asset account in the 14xx range)
  • 4500 Investment income — coupons, dividends, realised gains/losses
  • bank account FK on each transaction (cash side of purchases/sales)

The investment_account FK approach lets the CFO classify a row as
FVTPL/FVOCI/AC and route its postings to whichever GL account they've
created for that classification — without me hardcoding 1400/1410/1420.

What's deliberately NOT in v1:
  • OCI movement bifurcation for FVOCI (P&L vs OCI split on FV changes)
  • Effective interest method amortisation for AC
  • Multi-currency revaluation (use existing fx app at period end)
  • Maturity ladder / liquidity bucket reporting
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditableMixin, BaseModel


ZERO = Decimal('0.00')


def _next_investment_number():
    year = timezone.now().year
    prefix = f'INV-{year}-'
    with transaction.atomic():
        last = (
            Investment.objects.select_for_update()
            .filter(investment_number__startswith=prefix)
            .order_by('-investment_number')
            .values_list('investment_number', flat=True).first()
        )
        seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:06d}'


def _next_transaction_number():
    year = timezone.now().year
    prefix = f'INVTX-{year}-'
    with transaction.atomic():
        last = (
            InvestmentTransaction.objects.select_for_update()
            .filter(transaction_number__startswith=prefix)
            .order_by('-transaction_number')
            .values_list('transaction_number', flat=True).first()
        )
        seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:06d}'


# ---------------------------------------------------------------------------
# Investment
# ---------------------------------------------------------------------------

class Investment(AuditableMixin, BaseModel):
    """A single invested-asset position.

    The same instrument held in two different IFRS 9 classifications is
    two rows (so each can have its own GL account and FV mechanics).
    """

    class InstrumentType(models.TextChoices):
        TREASURY_BILL  = 'treasury_bill',  'Treasury Bill'
        GOVT_BOND      = 'govt_bond',      'Government Bond'
        CORP_BOND      = 'corp_bond',      'Corporate Bond'
        EQUITY         = 'equity',         'Equity / Shares'
        UNIT_TRUST     = 'unit_trust',     'Unit Trust / Mutual Fund'
        FIXED_DEPOSIT  = 'fixed_deposit',  'Fixed Deposit'
        OTHER          = 'other',          'Other'

    class Classification(models.TextChoices):
        FVTPL          = 'fvtpl',          'Fair Value Through P&L'
        FVOCI          = 'fvoci',          'Fair Value Through OCI'
        AMORTISED_COST = 'amortised_cost', 'Amortised Cost'

    class Status(models.TextChoices):
        OPEN     = 'open',     'Open'
        MATURED  = 'matured',  'Matured'
        DISPOSED = 'disposed', 'Disposed'

    investment_number = models.CharField(max_length=20, unique=True, editable=False)
    name = models.CharField(max_length=200)
    isin_or_ref = models.CharField(
        max_length=80, blank=True,
        help_text='ISIN, ticker, or any external reference.',
    )
    instrument_type = models.CharField(max_length=20, choices=InstrumentType.choices)
    classification = models.CharField(
        max_length=20, choices=Classification.choices,
        help_text='IFRS 9 classification — drives FV mechanics.',
    )

    issuer = models.CharField(max_length=200)
    custodian = models.CharField(
        max_length=200, blank=True,
        help_text='Bank or broker holding the instrument on our behalf.',
    )
    currency_code = models.ForeignKey(
        'core.Currency', on_delete=models.PROTECT, default='BWP',
    )

    face_value = models.DecimalField(
        max_digits=18, decimal_places=2, validators=[MinValueValidator(ZERO)],
        help_text='Nominal value (e.g. P1,000,000 face for a T-bill).',
    )
    cost = models.DecimalField(
        max_digits=18, decimal_places=2, validators=[MinValueValidator(ZERO)],
        help_text='Initial acquisition cost (clean price + fees).',
    )
    current_fair_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO,
        help_text='Latest mark-to-market — moved by FV adjustment transactions.',
    )

    coupon_rate_percent = models.DecimalField(
        max_digits=8, decimal_places=4, null=True, blank=True,
        help_text='Annual coupon (debt only).',
    )
    purchase_date = models.DateField()
    maturity_date = models.DateField(null=True, blank=True)

    investment_account = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        related_name='investments_carried',
        help_text='Balance-sheet asset GL account that carries this position.',
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-purchase_date', '-investment_number']
        indexes = [
            models.Index(fields=['classification']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.investment_number} — {self.name}'

    def save(self, *args, **kwargs):
        if not self.investment_number:
            self.investment_number = _next_investment_number()
        if not self.current_fair_value:
            self.current_fair_value = self.cost
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

class InvestmentTransaction(AuditableMixin, BaseModel):
    """A single movement on an Investment.

    Five types map to fairly common bookings:

      PURCHASE       DR investment / CR bank
      SALE           DR bank / CR investment (carrying), with realised gain
                     or loss going to 4500 Investment income (P&L)
      COUPON         DR bank / CR 4500 Investment income
      FAIR_VALUE     DR or CR investment depending on direction
                     (FVTPL: contra to 4500; FVOCI: TBD — split to OCI)
      MATURITY       DR bank / CR investment for the redemption proceeds
    """

    class TransactionType(models.TextChoices):
        PURCHASE   = 'purchase',   'Purchase'
        SALE       = 'sale',       'Sale'
        COUPON     = 'coupon',     'Coupon / Dividend / Interest'
        FAIR_VALUE = 'fair_value', 'Fair Value Adjustment'
        MATURITY   = 'maturity',   'Maturity / Redemption'

    class Status(models.TextChoices):
        DRAFT  = 'draft',  'Draft'
        POSTED = 'posted', 'Posted'

    transaction_number = models.CharField(max_length=20, unique=True, editable=False)
    investment = models.ForeignKey(
        Investment, on_delete=models.PROTECT, related_name='transactions',
    )
    transaction_type = models.CharField(max_length=15, choices=TransactionType.choices)
    transaction_date = models.DateField(default=timezone.localdate)

    amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text='Cash side of the transaction. For FV adjustments, the '
                  'unrealised gain (positive) or loss (negative).',
    )
    cash_account = models.ForeignKey(
        'ledger.Account', null=True, blank=True,
        on_delete=models.PROTECT, related_name='investment_cash_legs',
        help_text='Bank account for cash side. Not required for fair-value '
                  'adjustments.',
    )
    description = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        'ledger.JournalEntry', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='investment_transaction',
    )
    posted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='investment_transactions_posted',
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-transaction_date', '-transaction_number']
        indexes = [
            models.Index(fields=['transaction_type']),
            models.Index(fields=['investment', 'status']),
        ]

    def __str__(self):
        return f'{self.transaction_number} — {self.transaction_type}'

    def clean(self):
        cash_required = self.transaction_type in {
            self.TransactionType.PURCHASE,
            self.TransactionType.SALE,
            self.TransactionType.COUPON,
            self.TransactionType.MATURITY,
        }
        if cash_required and not self.cash_account_id:
            raise ValidationError({
                'cash_account': f'A cash account is required for {self.transaction_type}.',
            })
        if self.amount == ZERO:
            raise ValidationError({'amount': 'Amount cannot be zero.'})

    def save(self, *args, **kwargs):
        if not self.transaction_number:
            self.transaction_number = _next_transaction_number()
        super().save(*args, **kwargs)
