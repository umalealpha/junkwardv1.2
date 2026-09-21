"""
company_cards/models.py — company credit-card spending (CFO 2026-08-07).

WHY THIS IS NOT THE REFUND FLOW. "Snap a receipt" (hris.expense_api) is a
REIMBURSEMENT: the employee paid, so somebody owes them money and an accountant
has to pay it. A company card is the opposite — the company already paid. There
is nothing to approve and nobody to reimburse. What is missing is the paperwork:
which spend was what, coded to which account, with the receipt attached, and
which statement lines have no receipt at all.

CFO decisions, 2026-08-07:
  * Cardholders UPLOAD only. Finance codes the GL account afterwards.
    The uploader still types one line of "what for", because Finance chasing
    people to find out what a receipt was IS the problem, in a different shape.
  * NO approval step. The money is gone; a signature changes nothing.
  * The monthly statement IS loaded and matched, so Omni can name the
    transactions nobody has produced a receipt for. Without that leg nobody
    ever knows what is missing.
  * The long-overdue-task gate NEVER applies here — uploading a receipt is a
    duty, not a request, and making it harder would mean fewer receipts.

Card numbers are NEVER stored. A card is identified by its last four digits and
a human label ("CFO Visa"), which is all a reconciliation needs.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel

# A bill is "explained" once the cardholder has written at least this many words
# about it (CFO 2026-09-05: "each bill needs a 25-word description" — cut from 50
# because executives will not write 50). The photo is NEVER gated on this; a slip
# can be snapped now and explained when nudged. This is the "complete" gate, not
# the "capture" gate.
EXPLANATION_WORDS = 25


class CompanyCard(AuditableMixin, BaseModel):
    """One physical company card. Never holds a full card number."""

    label     = models.CharField(
        max_length=60,
        help_text='What people call it, e.g. "CFO Visa".')
    last4     = models.CharField(
        max_length=4,
        help_text='Last FOUR digits only — never the full number.')
    holder    = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='company_cards')
    company   = models.ForeignKey(
        'core.Company', on_delete=models.PROTECT,
        related_name='company_cards', null=True, blank=True)
    currency  = models.CharField(max_length=3, default='BWP')
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['label']
        constraints = [
            models.UniqueConstraint(fields=['last4', 'holder'],
                                    name='company_card_last4_holder_uniq'),
        ]

    def __str__(self):
        return f'{self.label} ••••{self.last4}'


class CardSpend(AuditableMixin, BaseModel):
    """One transaction on a company card, with its receipt."""

    class Status(models.TextChoices):
        UNCODED = 'uncoded', 'Waiting for Finance to code'
        CODED   = 'coded',   'Coded'
        QUERIED = 'queried', 'Finance has a question'

    card        = models.ForeignKey(
        CompanyCard, on_delete=models.PROTECT, related_name='spends')
    uploaded_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='card_spends')

    spent_on    = models.DateField()
    merchant    = models.CharField(max_length=120, blank=True, default='')
    amount      = models.DecimalField(max_digits=12, decimal_places=2)
    currency    = models.CharField(max_length=3, default='BWP')
    # Typed by the cardholder. Finance codes from this rather than phoning round
    # three weeks later (CFO 2026-08-07). Now needs >=EXPLANATION_WORDS words to
    # count as explained (CFO 2026-09-05); widened from 200 so a real few
    # sentences fit, spoken via the phone mic if typing is a chore.
    what_for    = models.CharField(
        max_length=1000,
        help_text='A few words — at least 25 — on what it was for. Finance codes '
                  'the account from this.')

    receipt     = models.FileField(upload_to='card-receipts/%Y/%m/', blank=True)

    status      = models.CharField(
        max_length=10, choices=Status.choices,
        default=Status.UNCODED, db_index=True)
    gl_account  = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        null=True, blank=True, related_name='card_spends')
    coded_by    = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='card_spends_coded')
    coded_at    = models.DateTimeField(null=True, blank=True)
    finance_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-spent_on', '-created_at']
        indexes = [
            models.Index(fields=['card', 'spent_on'], name='cardspend_card_date_idx'),
            models.Index(fields=['status'], name='cardspend_status_idx'),
        ]

    def __str__(self):
        return f'{self.card} · {self.currency} {self.amount} · {self.merchant or self.what_for[:30]}'

    @property
    def has_receipt(self) -> bool:
        return bool(self.receipt and self.receipt.name)

    @property
    def word_count(self) -> int:
        return len((self.what_for or '').split())

    @property
    def is_explained(self) -> bool:
        """Has the cardholder written a real reason (>=EXPLANATION_WORDS words)?"""
        return self.word_count >= EXPLANATION_WORDS


def normalise_description(raw: str) -> str:
    """Fold away the harmless differences between two printings of the SAME
    charge — case, leading/trailing space, runs of spaces (Laone Thebe
    2026-09-11). Deliberately nothing more: stripping punctuation or truncating
    would start merging genuinely different merchants, and a wrong duplicate
    call silently DROPS a real transaction, which is worse than importing one.
    """
    return ' '.join((raw or '').split()).casefold()


class CardStatement(AuditableMixin, BaseModel):
    """A month's card statement, loaded so Omni can name what has no receipt."""

    card        = models.ForeignKey(
        CompanyCard, on_delete=models.PROTECT, related_name='statements')
    period_year  = models.PositiveSmallIntegerField()
    period_month = models.PositiveSmallIntegerField()
    uploaded_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='card_statements')
    source_name = models.CharField(max_length=200, blank=True, default='')
    # SHA-256 of the uploaded bytes, so re-uploading the very same file can be
    # recognised and queried before it is processed again (Laone Thebe
    # 2026-09-11). Blank on every statement loaded before this field existed.
    file_hash   = models.CharField(max_length=64, blank=True, default='',
                                   db_index=True)

    class Meta(BaseModel.Meta):
        ordering = ['-period_year', '-period_month']
        constraints = [
            models.UniqueConstraint(fields=['card', 'period_year', 'period_month'],
                                    name='card_statement_period_uniq'),
        ]

    def __str__(self):
        return f'{self.card} · {self.period_year}-{self.period_month:02d}'


class CardStatementLine(BaseModel):
    """One line off the statement, matched to a CardSpend where we can."""

    statement   = models.ForeignKey(
        CardStatement, on_delete=models.CASCADE, related_name='lines')
    posted_on   = models.DateField()
    description = models.CharField(max_length=250, blank=True, default='')
    amount      = models.DecimalField(max_digits=12, decimal_places=2)

    matched_spend = models.OneToOneField(
        CardSpend, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='statement_line')
    # A human said "this line needs no receipt" (a card fee, a reversal).
    waived      = models.BooleanField(default=False)
    waived_note = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['posted_on', 'id']
        indexes = [
            models.Index(fields=['statement', 'posted_on'],
                         name='cardline_stmt_date_idx'),
        ]

    def __str__(self):
        return f'{self.posted_on} · {self.amount} · {self.description[:40]}'

    @property
    def needs_receipt(self) -> bool:
        """A line nobody has produced a receipt for, and nobody has waived."""
        return not self.waived and self.matched_spend_id is None
