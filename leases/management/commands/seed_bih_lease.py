"""
Seed the BIH ICON Building lease from Kago's "IFRS 16 ALPHA — Corrected FY26"
workbook (email 2026-07-15). Idempotent: keyed on the lease name, updates inputs
in place. Rate 6.76% is Kago's management input (not the 7% shown in the Inputs
tab) — it's the rate the schedule actually discounts at, verified to the cent.

    python manage.py seed_bih_lease
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from leases.models import Lease


class Command(BaseCommand):
    help = "Seed / refresh the BIH ICON Building IFRS 16 lease."

    def handle(self, *args, **opts):
        company = None
        try:
            from core.models import Company
            company = (Company.objects.filter(code__iexact="ADIC").first()
                       or Company.objects.filter(name__icontains="Alpha Direct").first())
        except Exception:  # noqa: BLE001
            pass

        defaults = dict(
            property_ref="BIH ICON Building — LOI cl. 4.2/4.3",
            company=company,
            commencement_date=date(2023, 3, 1),
            term_months=60,
            monthly_payment=Decimal("78713.50"),
            escalation_pct=Decimal("4"),
            discount_rate_pct=Decimal("6.76"),
            fye_month=6,
            payment_timing=Lease.Timing.ARREARS,
            incentives=Decimal("3933801"),
            initial_direct_costs=Decimal("0"),
            prepaid=Decimal("0"),
            dismantle=Decimal("0"),
            notes=("Botswana Innovation Hub ICON Building lease. Capitalised payment "
                   "includes operating costs (P10,287.50) + parking (P10,816) under the "
                   "IFRS 16 practical expedient to combine lease + non-lease components. "
                   "Discounted in arrears (GL basis since 2023). Rate 6.76% = management "
                   "input. Corrected FY26 per Kago Tshutlhedi, 15 Jul 2026."),
        )
        obj, created = Lease.objects.update_or_create(name="BIH ICON Building", defaults=defaults)
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} lease: {obj.name} ({obj.id})"))
