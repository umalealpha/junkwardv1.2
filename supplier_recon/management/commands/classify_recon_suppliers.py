"""Create ReconSupplierProfile rows for vendors that actually have bills.

Without a profile a supplier never appears on the reconciliation board, so a
fresh install shows an empty page. This seeds the register from real history:

    python manage.py classify_recon_suppliers --dry-run
    python manage.py classify_recon_suppliers
    python manage.py classify_recon_suppliers --company ADIC --since 2026-01-01

Category is inferred conservatively:
  * a vendor whose purchase orders are mostly raised by CLAIMS is classified
    ``claims_other`` — claim-backed, so its bills must tie to a claim
    authorisation;
  * everything else is ``general``.

The bias is deliberate. Marking a claims supplier as ``general`` only means one
missing flag; marking an office supplier as a panel beater would raise a false
"No Claim Authorisation" alarm on every bill. Payables refines the exact
category (panel beater / parts / towing / assessor / glass / medical) in admin
or on the supplier-profiles API — this command never overwrites a category a
human has already set.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from billing.models import Contact, Invoice
from core.models import Company
from procurement.models import PurchaseOrder

from ...constants import SupplierCategory
from ...models import ReconSupplierProfile


class Command(BaseCommand):
    help = 'Seed supplier reconciliation profiles from real billing history.'

    def add_arguments(self, parser):
        parser.add_argument('--company', default=None,
                            help='Company code. Defaults to every entity.')
        parser.add_argument('--since', default=None,
                            help='Only consider bills issued on/after YYYY-MM-DD.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be created, change nothing.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        code = options['company']

        vendors = Contact.objects.filter(
            contact_type__in=[Contact.ContactType.VENDOR,
                              Contact.ContactType.BROKER,
                              Contact.ContactType.REINSURER],
            is_active=True,
        )
        if code:
            company = Company.objects.filter(code__iexact=code).first()
            if company is None:
                self.stderr.write(self.style.ERROR(f"No company '{code}'."))
                return
            vendors = vendors.filter(company=company)

        # The filter on an aggregate over a reverse relation must be written
        # from the OUTER model, so every lookup carries the `invoices__` path.
        # Without it Django resolves `invoice_type` against Contact and raises
        # FieldError (hit on the first prod run, 2026-07-25).
        bill_filter = Q(invoices__invoice_type=Invoice.InvoiceType.VENDOR_BILL)
        if options['since']:
            bill_filter &= Q(invoices__issue_date__gte=options['since'])

        vendors = (vendors
                   .annotate(bill_count=Count('invoices',
                                              filter=bill_filter, distinct=True))
                   .filter(bill_count__gt=0)
                   .order_by('name'))

        already = set(ReconSupplierProfile.objects
                      .values_list('contact_id', flat=True))

        created = claims_backed = skipped = 0
        for vendor in vendors:
            if vendor.id in already:
                skipped += 1
                continue

            claims_pos = PurchaseOrder.objects.filter(
                supplier=vendor,
                department=PurchaseOrder.Department.CLAIMS).count()
            total_pos = PurchaseOrder.objects.filter(supplier=vendor).count()
            is_claims = total_pos > 0 and claims_pos * 2 >= total_pos

            category = (SupplierCategory.CLAIMS_OTHER if is_claims
                        else SupplierCategory.GENERAL)

            if dry:
                self.stdout.write(
                    f"  would add {vendor.name} -> {category} "
                    f"({vendor.bill_count} bills, {claims_pos}/{total_pos} claims POs)")
            else:
                ReconSupplierProfile.objects.create(
                    contact=vendor, category=category, in_scope=True,
                    notes='Seeded by classify_recon_suppliers from PO history. '
                          'Refine the exact category in admin.')
            created += 1
            if is_claims:
                claims_backed += 1

        verb = 'Would create' if dry else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {created} profile(s) — {claims_backed} claim-backed, "
            f"{created - claims_backed} general. Skipped {skipped} already classified."))
        if not dry and created:
            self.stdout.write(
                'Next: payables refines panel beater / parts / towing / assessor '
                'in Django admin, then build a month.')
