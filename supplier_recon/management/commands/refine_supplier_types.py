"""Refine supplier trade from the vendor's name.

CFO directive 2026-07-25: "just build using assumptions we will fix later."
``classify_recon_suppliers`` only decides claim-backed vs general from PO
history; it leaves the exact trade as ``claims_other``. This narrows that to
panel beater / parts / towing / glass / assessor / medical using keywords in the
vendor name, so the board reads properly from day one.

These are ASSUMPTIONS, and the command says so:
  * every row it changes gets a note stamped "Trade inferred from the supplier
    name (assumption, 2026-07-25) - correct in admin if wrong", so nobody
    mistakes a guess for a verified classification;
  * it only ever narrows WITHIN claim-backed (``claims_other`` -> a specific
    trade). It never moves a supplier into or out of claim-backed, because that
    changes whether the claim-authorisation rule fires;
  * a row a human has already set to a specific trade is never touched
    (recognised by the absence of the seeder's note marker).

    python manage.py refine_supplier_types --dry-run
    python manage.py refine_supplier_types
    python manage.py refine_supplier_types --company ADIC
"""

import re

from django.core.management.base import BaseCommand

from core.models import Company

from ...constants import SupplierCategory
from ...models import ReconSupplierProfile

SEEDED_MARKER = 'Seeded by classify_recon_suppliers'
INFERRED_NOTE = ('Trade inferred from the supplier name (assumption, '
                 '2026-07-25) - correct in admin if wrong.')

# Ordered: the first pattern that matches wins, so the more specific trades are
# tested before the broad "motors" catch-all.
RULES = [
    # \w* on the stems: the prod list has "PG WINDSCREENS" (plural), which a
    # bare \bwindscreen\b misses.
    (SupplierCategory.GLASS,        r'\b(glass\w*|windscreen\w*|windshield\w*)\b'),
    (SupplierCategory.TOWING,       r'\b(tow|towing|recovery|recover)\b'),
    (SupplierCategory.ASSESSOR,     r'\b(assessor|assessors|assessment|loss adjust\w*)\b'),
    (SupplierCategory.MEDICAL,      r'\b(clinic|hospital|medical|pharmac\w*|surgery|health)\b'),
    (SupplierCategory.PANEL_BEATER, r'\b(panel\s*beater\w*|panelbeater\w*|body\s*shop|'
                                    r'spray\s*paint\w*|auto\s*body|detailing)\b'),
    (SupplierCategory.PARTS,        r'\b(spare\w*|parts|accessor\w*|tyre\w*|tire\w*|'
                                    r'battery|batteries|exhaust|radiator)\b'),
    # Dealers / workshops / general motor trade: a panel beater in practice for
    # claims work, which is the category the board cares about.
    # Deliberately does NOT include a bare "service(s)" keyword: that caught
    # "TATSAND MINING SERVICES" and would have tagged a mining contractor as a
    # panel beater. Real motor names still match on auto / motors / wheels.
    (SupplierCategory.PANEL_BEATER, r'\b(motors|motor|auto|autos|automotive|toyota|'
                                    r'nissan|ford|mazda|hino|wheels|garage|workshop|'
                                    r'mobility|panel)\b'),
]


def infer(name: str):
    """Return a specific trade for this supplier name, or None if unclear."""
    low = f' {(name or "").lower()} '
    for category, pattern in RULES:
        if re.search(pattern, low):
            return category
    return None


class Command(BaseCommand):
    help = "Narrow claim-backed suppliers to a specific trade using their name."

    def add_arguments(self, parser):
        parser.add_argument('--company', default=None,
                            help='Company code. Defaults to every entity.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change, change nothing.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        code = options['company']

        # Only rows the seeder created, and only the unconfirmed trade. A
        # specific trade a person chose is left alone.
        qs = (ReconSupplierProfile.objects
              .filter(category=SupplierCategory.CLAIMS_OTHER,
                      notes__startswith=SEEDED_MARKER)
              .select_related('contact')
              .order_by('contact__name'))
        if code:
            company = Company.objects.filter(code__iexact=code).first()
            if company is None:
                self.stderr.write(self.style.ERROR(f"No company '{code}'."))
                return
            qs = qs.filter(contact__company=company)

        changed = unclear = 0
        by_trade: dict[str, int] = {}
        for profile in qs:
            trade = infer(profile.contact.name)
            if trade is None:
                unclear += 1
                self.stdout.write(
                    f"  UNCLEAR  {profile.contact.name[:52]:52s} "
                    f"-> left as claims_other")
                continue
            by_trade[trade] = by_trade.get(trade, 0) + 1
            changed += 1
            verb = 'would set' if dry else 'set'
            self.stdout.write(
                f"  {verb:9s} {profile.contact.name[:52]:52s} -> {trade}")
            if not dry:
                profile.category = trade
                profile.notes = f"{SEEDED_MARKER}. {INFERRED_NOTE}"
                profile.save(update_fields=['category', 'notes', 'updated_at'])

        self.stdout.write('')
        for trade, n in sorted(by_trade.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {trade:14s} {n}")
        verb = 'Would refine' if dry else 'Refined'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {changed} supplier(s); {unclear} left as claims_other "
            f"(still claim-backed, so the claim rule still applies)."))
        if dry:
            self.stdout.write(self.style.WARNING('Dry run - nothing written.'))
        else:
            self.stdout.write(
                'These are name-based assumptions. Payables should correct any '
                'that are wrong in Django admin (Recon Supplier Profiles).')
