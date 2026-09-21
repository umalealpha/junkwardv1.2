"""
banking/rec_rules_models.py

Bank reconciliation rule engine — models.

BankRecRule
-----------
Declarative rules used by `banking.rec_engine.match_statement_lines` to either
auto-post a JE or propose one for an unmatched BankStatementLine.

Rules are evaluated in ascending `priority` order; the first match wins. A rule
matches a line when ALL of the following hold:

  * `is_active` is True
  * `description_regex` (case-insensitive) finds a match in line.description
  * `amount_min` (inclusive) <= line.amount when set
  * line.amount <= `amount_max` (inclusive) when set
  * the rule's `company` matches the bank account's owner company

Imported into `banking/models.py` so Django picks the model up under the
`banking` app label and the existing admin / serializers can reach it via the
package-level namespace.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class BankRecRule(BaseModel):
    """A declarative bank-reconciliation rule."""

    class Action(models.TextChoices):
        AUTO_JE  = 'auto_je',  'Auto-post draft JE'
        PROPOSE  = 'propose',  'Propose only (no JE)'

    company           = models.ForeignKey(
                            'core.Company', on_delete=models.PROTECT,
                            related_name='bank_rec_rules',
                        )
    priority          = models.IntegerField(
                            default=100,
                            help_text='Lower number = higher priority. First match wins.',
                        )
    description_regex = models.CharField(
                            max_length=500,
                            help_text='Case-insensitive Python regex matched against '
                                      'BankStatementLine.description.',
                        )
    amount_min        = models.DecimalField(
                            max_digits=18, decimal_places=2, null=True, blank=True,
                            help_text='Inclusive minimum signed amount. NULL = no lower bound.',
                        )
    amount_max        = models.DecimalField(
                            max_digits=18, decimal_places=2, null=True, blank=True,
                            help_text='Inclusive maximum signed amount. NULL = no upper bound.',
                        )
    target_account    = models.ForeignKey(
                            'ledger.Account', on_delete=models.PROTECT,
                            related_name='bank_rec_rules',
                            help_text='Counter-leg GL account used when a draft JE is built.',
                        )
    target_contact    = models.ForeignKey(
                            'billing.Contact', on_delete=models.SET_NULL,
                            null=True, blank=True,
                            related_name='bank_rec_rules',
                            help_text='Optional contact tagged on the JE line.',
                        )
    action            = models.CharField(
                            max_length=10, choices=Action.choices,
                            default=Action.AUTO_JE,
                        )
    is_active         = models.BooleanField(default=True)
    created_by        = models.ForeignKey(
                            User, on_delete=models.PROTECT,
                            related_name='bank_rec_rules_created',
                        )

    class Meta(BaseModel.Meta):
        ordering            = ['priority', 'created_at']
        verbose_name        = 'Bank Reconciliation Rule'
        verbose_name_plural = 'Bank Reconciliation Rules'
        indexes = [
            models.Index(fields=['company', 'is_active', 'priority']),
        ]

    def __str__(self):
        return f"[{self.priority}] {self.description_regex} -> {self.target_account_id}"
