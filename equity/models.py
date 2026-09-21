"""equity/models.py — the live, editable cap table + ESOP register.

Replaces the frozen constants in reporting/equity_data.py with database records
so Finance can maintain the register in omni, vesting can be tracked per grant,
and each holder can see their own equity through a self-service page. Structured
along Open Cap Format (OCF) concepts (Stakeholder / StockIssuance / StockPlan)
so the register speaks the same industry model Carta uses.

Board / exec-comp data — the API that serves it stays whitelist-gated to
EXCO / Finance / admin (see equity/api_views.py). Off-GL; the GL equity leg is
Odoo account 307000 (see reporting/equity_data.ODOO_RECON).
"""
from __future__ import annotations

from django.db import models


class Stakeholder(models.Model):
    """A unique individual or entity on the capitalisation table.

    This is exactly Carta's "Security Holder" — a shareholder, an ESOP option
    holder, or both. The count of Stakeholders drives Carta's fee tier, so
    `is_current` lets Finance retire a fully-exited holder without deleting the
    history behind them.
    """
    KIND = [('individual', 'Individual'), ('entity', 'Entity')]

    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=KIND, default='individual')
    # Link to the staff record so a logged-in employee can be matched to their
    # own grant. Null for external investors and entities.
    employee = models.ForeignKey(
        'payroll.Employee', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='equity_stakeholders',
    )
    email = models.EmailField(blank=True, help_text='Used to match a login when no staff record is linked.')
    is_current = models.BooleanField(
        default=True,
        help_text='Uncheck for a former holder whose shares and grants have fully exited.',
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ShareHolding(models.Model):
    """Issued shares held by a stakeholder in a given share class (the cap table)."""
    stakeholder = models.ForeignKey(Stakeholder, on_delete=models.CASCADE, related_name='holdings')
    klass = models.CharField(max_length=40, help_text='Share class label, e.g. ORB, ORA, PA, ORB+ORA.')
    shares = models.BigIntegerField(default=0)
    usd_invested = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-shares']

    def __str__(self):
        return f"{self.stakeholder.name} — {self.klass} {self.shares:,}"


class EsopGrant(models.Model):
    """An option/RSU grant to a stakeholder under the 2021 Stock Ownership & Option Plan."""
    STATUS = [
        ('active', 'Active'),
        ('lapsed', 'Lapsed'),
        ('exercised', 'Exercised'),
        ('cancelled', 'Cancelled'),
    ]
    stakeholder = models.ForeignKey(Stakeholder, on_delete=models.CASCADE, related_name='grants')
    units = models.IntegerField(default=0)
    grant_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    exercise_price_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    # Kept as stored figures so the register reconciles to the Carta snapshot to
    # the decimal; recomputed by Finance when units change materially.
    pct_pool = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    pct_fd = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    status = models.CharField(max_length=12, choices=STATUS, default='active')
    letter_ref = models.CharField(max_length=120, blank=True, help_text='Letter of Grant reference.')
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-units']

    def __str__(self):
        return f"{self.stakeholder.name} — {self.units:,} units"


class VestingTranche(models.Model):
    """One vesting step of a grant. A grant with no tranches is 'per letter of grant'
    until Finance loads the schedule — we never fabricate vesting we do not hold."""
    grant = models.ForeignKey(EsopGrant, on_delete=models.CASCADE, related_name='tranches')
    vest_date = models.DateField()
    units = models.IntegerField(default=0)
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['vest_date']

    def __str__(self):
        return f"{self.grant.stakeholder.name} — {self.units:,} on {self.vest_date}"
