"""
leases/models.py — IFRS 16 lease register (CFO / Kago 2026-07-15).

One row per lease (property, vehicle, equipment). All the accounting numbers —
right-of-use asset, lease liability, the amortisation + depreciation schedules,
and the current/non-current split at each financial year-end — are DERIVED from
these inputs by leases/engine.py (never stored), so a single edit (change the
escalation, the rate, the term) recomputes the whole picture. Mirrors the Budget
Library's deterministic-engine style.

Seeded from Kago's "IFRS 16 ALPHA — Corrected FY26" workbook (BIH ICON Building).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models

from core.models import BaseModel


class Lease(BaseModel):
    class Timing(models.TextChoices):
        ARREARS = "arrears", "In arrears (month-end)"
        ADVANCE = "advance", "In advance (month-start)"

    name            = models.CharField(max_length=160, help_text='e.g. "BIH ICON Building"')
    property_ref    = models.CharField(max_length=160, blank=True, default="",
                                       help_text="LOI clause / property code / lessor")
    company         = models.ForeignKey("core.Company", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="leases")

    commencement_date = models.DateField()
    term_months       = models.PositiveIntegerField(default=60)
    monthly_payment   = models.DecimalField(max_digits=14, decimal_places=2,
                                            help_text="Base monthly payment, excl. VAT")
    escalation_pct    = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"),
                                            help_text="Annual escalation %, compounded on the anniversary")
    discount_rate_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("7"),
                                            help_text="Incremental borrowing rate, % p.a.")
    fye_month         = models.PositiveSmallIntegerField(default=6, help_text="Financial year-end month (6 = 30 June)")
    payment_timing    = models.CharField(max_length=8, choices=Timing.choices, default=Timing.ARREARS)

    incentives            = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"),
                                                help_text="Lease incentives received (reduces ROU)")
    initial_direct_costs  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    prepaid               = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    dismantle             = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    notes           = models.TextField(blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ["name"]
        verbose_name = "IFRS 16 Lease"
        verbose_name_plural = "IFRS 16 Leases"

    def __str__(self):
        return self.name
