"""
Management command: seed_demo_po

Creates a demonstration USD-denominated Purchase Order to showcase the
procurement workflow end-to-end. Idempotent — safe to run multiple times.

  python manage.py seed_demo_po

Creates:
  - Currency record for USD (if missing)
  - Exchange rate USD->BWP at 13.5 (rough indicative)
  - Vendor contact "ACME Software Solutions LLC" (if missing)
  - Account 6300 must already exist in CoA
  - One PO in USD (Operations dept) with two lines
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import Contact
from core.models import Currency, ExchangeRate, TaxRate
from ledger.models import Account, FiscalPeriod
from procurement.models import PurchaseOrder, PurchaseOrderLine
from django.utils import timezone


DEMO_PO_REF_TAG = 'DEMO-USD-PO'


class Command(BaseCommand):
    help = 'Seed a demonstration USD-denominated Purchase Order.'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('Seeding demo USD Purchase Order...'))

        # 1) Currency
        usd, created = Currency.objects.get_or_create(
            code='USD',
            defaults={'name': 'US Dollar', 'symbol': '$', 'decimal_places': 2},
        )
        self.stdout.write(f"  USD currency: {'created' if created else 'exists'}")

        bwp, _ = Currency.objects.get_or_create(
            code='BWP',
            defaults={'name': 'Botswana Pula', 'symbol': 'P', 'decimal_places': 2},
        )

        # 2) Exchange rate (indicative)
        today = timezone.localdate()
        ExchangeRate.objects.get_or_create(
            from_currency=usd, to_currency=bwp, effective_date=today,
            defaults={'rate': Decimal('13.50000000'), 'source': ExchangeRate.Source.MANUAL},
        )
        self.stdout.write(f"  USD->BWP rate for {today}: 13.50")

        # 3) Vendor
        supplier, created = Contact.objects.get_or_create(
            name='ACME Software Solutions LLC',
            contact_type=Contact.ContactType.VENDOR,
            defaults={
                'currency_code': usd,
                'email': 'billing@acme-software-demo.example',
                'address': '500 Tech Park Drive, Austin TX 78701, USA',
                'is_resident': False,
                'payment_terms_days': 30,
            },
        )
        self.stdout.write(f"  Vendor 'ACME Software Solutions LLC': {'created' if created else 'exists'}")

        # 4) Expense account (6300 IT and software)
        try:
            it_account = Account.objects.get(code='6300')
        except Account.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                "Account 6300 (IT and software) not found. "
                "Run setup_chart_of_accounts first."
            ))
            return

        # 5) Tax (zero-rated for non-resident vendor)
        zero_tax, _ = TaxRate.objects.get_or_create(
            tax_code='ZERO',
            defaults={
                'name': 'Zero-rated',
                'rate': Decimal('0.00'),
                'effective_from': datetime.date(2020, 1, 1),
            },
        )

        # 6) Creator user — first superuser, or skip
        creator = User.objects.filter(is_superuser=True).order_by('pk').first()
        if creator is None:
            self.stderr.write(self.style.ERROR(
                "No superuser found. Create one with createsuperuser first."
            ))
            return

        # 7) Don't duplicate the demo
        existing = PurchaseOrder.objects.filter(
            related_claim_reference=DEMO_PO_REF_TAG,
        ).first()
        if existing:
            self.stdout.write(self.style.WARNING(
                f"  Demo PO already exists: {existing.po_number} — skipping."
            ))
            return

        # 8) Create PO
        po = PurchaseOrder.objects.create(
            department=PurchaseOrder.Department.ADMIN,
            supplier=supplier,
            issue_date=today,
            expected_delivery_date=today + datetime.timedelta(days=14),
            currency_code=usd,
            exchange_rate=Decimal('13.50000000'),
            related_claim_reference=DEMO_PO_REF_TAG,
            justification=(
                'Demonstration PO showing USD multi-currency procurement '
                'with FM + CFO joint approval. Annual SaaS subscription '
                'and one-time implementation services.'
            ),
            created_by=creator,
        )

        PurchaseOrderLine.objects.create(
            purchase_order=po,
            description='Annual SaaS subscription — Premium tier',
            account=it_account,
            quantity=Decimal('1.0000'),
            unit_price=Decimal('12000.00'),
            tax_code=zero_tax,
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po,
            description='Implementation services — 40 hours',
            account=it_account,
            quantity=Decimal('40.0000'),
            unit_price=Decimal('150.00'),
            tax_code=zero_tax,
        )

        po.recalculate_totals()
        po.save(audit_user=creator, audit_description=f"Seeded demo PO {po.po_number}")

        self.stdout.write(self.style.SUCCESS(
            f"\n  Created PO {po.po_number}:\n"
            f"    Supplier:       {supplier.name}\n"
            f"    Currency:       USD (rate 13.50 -> BWP)\n"
            f"    Subtotal:       ${po.subtotal:,.2f}\n"
            f"    Total (USD):    ${po.total_amount:,.2f}\n"
            f"    Total (BWP):    P{po.total_bwp:,.2f}\n"
            f"    Status:         {po.get_status_display()}\n"
        ))
        self.stdout.write(self.style.SUCCESS('Done.'))
