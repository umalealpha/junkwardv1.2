"""
Management command: create_sample_data

Populates the Alpha Direct system with realistic insurance company financial
data suitable for demo / stakeholder viewing.

Creates:
  - 12 contacts (customers, vendors, brokers)
  - 25+ invoices spanning the fiscal year (Jul 2025 - Mar 2026)
  - Payments against many of the invoices (full and partial)
  - Mix of statuses: draft, posted, partially paid, paid, overdue

Target dashboard numbers:
  - GWP (Gross Written Premium): ~BWP 12M
  - Claims incurred:             ~BWP 4M
  - Total cash (bank):           ~BWP 4-5M
  - Outstanding receivables:     ~BWP 3-4M

The command is idempotent: it checks for a sentinel contact before creating
anything and skips if data already exists.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from billing.models import Contact, Invoice, InvoiceLine
from core.models import Currency, TaxRate
from ledger.models import Account
from payments.models import Payment, PaymentAllocation


D = Decimal
ZERO = Decimal('0.00')

SENTINEL_NAME = 'Botswana Power Corporation (Sample)'


# ── Contact definitions ─────────────────────────────────────────────────

CONTACTS = [
    # Customers (insurance policyholders / corporates)
    {
        'name': 'Botswana Power Corporation (Sample)',
        'contact_type': 'customer',
        'registration_number': 'BW-00012345',
        'tax_id': 'TIN-501234567',
        'email': 'finance@bpc.bw',
        'phone': '+267 360 0000',
        'address': 'Plot 1, Macheng Way, Gaborone, Botswana',
        'payment_terms_days': 30,
    },
    {
        'name': 'Debswana Diamond Company (Sample)',
        'contact_type': 'customer',
        'registration_number': 'BW-00023456',
        'tax_id': 'TIN-502345678',
        'email': 'accounts@debswana.bw',
        'phone': '+267 290 4000',
        'address': 'Plot 2, Debswana House, Gaborone',
        'payment_terms_days': 30,
    },
    {
        'name': 'First National Bank Botswana (Sample)',
        'contact_type': 'customer',
        'registration_number': 'BW-00034567',
        'tax_id': 'TIN-503456789',
        'email': 'insurance@fnb.co.bw',
        'phone': '+267 370 2000',
        'address': '5th Floor, FNB House, Gaborone',
        'payment_terms_days': 30,
    },
    {
        'name': 'Choppies Enterprises (Sample)',
        'contact_type': 'customer',
        'registration_number': 'BW-00045678',
        'tax_id': 'TIN-504567890',
        'email': 'finance@choppies.co.bw',
        'phone': '+267 392 2000',
        'address': 'Plot 12345, Pilane Road, Gaborone',
        'payment_terms_days': 30,
    },
    {
        'name': 'Wilderness Safaris Botswana (Sample)',
        'contact_type': 'customer',
        'registration_number': 'BW-00056789',
        'tax_id': 'TIN-505678901',
        'email': 'accounts@wilderness.co.bw',
        'phone': '+267 686 0086',
        'address': 'Plot 267, Maun, Botswana',
        'payment_terms_days': 45,
    },
    {
        'name': 'Mascom Wireless (Sample)',
        'contact_type': 'customer',
        'registration_number': 'BW-00067890',
        'tax_id': 'TIN-506789012',
        'email': 'corporate@mascom.bw',
        'phone': '+267 371 2000',
        'address': 'Plot 50350, Fairgrounds, Gaborone',
        'payment_terms_days': 30,
    },
    # Vendors (claims service providers)
    {
        'name': 'Gaborone Private Hospital (Sample)',
        'contact_type': 'vendor',
        'registration_number': 'BW-00078901',
        'tax_id': 'TIN-507890123',
        'email': 'billing@gph.co.bw',
        'phone': '+267 368 5000',
        'address': 'Plot 4534, Segoditshane Way, Gaborone',
        'payment_terms_days': 14,
    },
    {
        'name': 'Autobody Repairs BW (Sample)',
        'contact_type': 'vendor',
        'registration_number': 'BW-00089012',
        'tax_id': 'TIN-508901234',
        'email': 'claims@autobody.co.bw',
        'phone': '+267 391 3000',
        'address': 'Plot 221, Broadhurst Industrial, Gaborone',
        'payment_terms_days': 14,
    },
    {
        'name': 'Delta Property Assessors (Sample)',
        'contact_type': 'vendor',
        'registration_number': 'BW-00090123',
        'tax_id': 'TIN-509012345',
        'email': 'assess@deltaprop.co.bw',
        'phone': '+267 395 0000',
        'address': 'Plot 543, Main Mall, Gaborone',
        'payment_terms_days': 14,
    },
    # Brokers
    {
        'name': 'AON Botswana (Sample)',
        'contact_type': 'broker',
        'registration_number': 'BW-00101234',
        'tax_id': 'TIN-510123456',
        'email': 'commission@aon.co.bw',
        'phone': '+267 395 4800',
        'address': 'Plot 64518, Fairgrounds, Gaborone',
        'payment_terms_days': 30,
        'wht_exempt': False,
    },
    {
        'name': 'Marsh Botswana (Sample)',
        'contact_type': 'broker',
        'registration_number': 'BW-00112345',
        'tax_id': 'TIN-511234567',
        'email': 'finance@marsh.co.bw',
        'phone': '+267 390 1234',
        'address': 'Plot 111, CBD, Gaborone',
        'payment_terms_days': 30,
        'wht_exempt': False,
    },
    {
        'name': 'Glenrand MIB Botswana (Sample)',
        'contact_type': 'broker',
        'registration_number': 'BW-00123456',
        'tax_id': 'TIN-512345678',
        'email': 'accounts@glenrand.co.bw',
        'phone': '+267 395 5000',
        'address': 'Plot 33, The Walk, Gaborone',
        'payment_terms_days': 30,
        'wht_exempt': False,
    },
]


# ── Invoice definitions ─────────────────────────────────────────────────
# Each dict describes an invoice to create. Amounts are the line unit_price
# (pre-tax). The system auto-calculates tax and totals.
#
# Status flow:
#   'post'          -> posted (no payment)
#   'pay_full'      -> posted + fully paid
#   'pay_partial'   -> posted + partially paid
#   'draft'         -> left as draft
#   'overdue'       -> posted, due_date in the past, not paid

INVOICES = [
    # ── Customer invoices (premiums) ────────────────────────────────────
    # BPC - large commercial property policy
    {
        'contact_name': 'Botswana Power Corporation (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 7, 15),
        'description': 'Commercial property insurance - annual premium',
        'account_code': '4100',
        'line_desc': 'Property all-risks cover - BPC infrastructure',
        'unit_price': D('1750000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 8, 10),
    },
    # Debswana - mining liability
    {
        'contact_name': 'Debswana Diamond Company (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 8, 1),
        'description': 'Mining operations liability insurance',
        'account_code': '4100',
        'line_desc': 'Mining liability cover - Jwaneng & Orapa mines',
        'unit_price': D('2100000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 8, 25),
    },
    # FNB - professional indemnity
    {
        'contact_name': 'First National Bank Botswana (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 9, 1),
        'description': 'Professional indemnity insurance',
        'account_code': '4100',
        'line_desc': 'Professional indemnity - banking operations',
        'unit_price': D('1200000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 9, 20),
    },
    # Choppies - commercial combined
    {
        'contact_name': 'Choppies Enterprises (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 10, 1),
        'description': 'Commercial combined - retail operations',
        'account_code': '4100',
        'line_desc': 'Commercial combined cover - all retail outlets',
        'unit_price': D('850000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_partial',
        'pay_date': datetime.date(2025, 11, 1),
        'pay_amount': D('500000.00'),
    },
    # Wilderness - tourism liability
    {
        'contact_name': 'Wilderness Safaris Botswana (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 10, 15),
        'description': 'Tourism operations liability',
        'account_code': '4100',
        'line_desc': 'Tourism & adventure activities liability cover',
        'unit_price': D('680000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'overdue',
    },
    # Mascom - technology E&O
    {
        'contact_name': 'Mascom Wireless (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 11, 1),
        'description': 'Technology errors & omissions insurance',
        'account_code': '4100',
        'line_desc': 'Technology E&O cover - telecom operations',
        'unit_price': D('950000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 11, 28),
    },
    # BPC - motor fleet renewal
    {
        'contact_name': 'Botswana Power Corporation (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2025, 12, 1),
        'description': 'Motor fleet insurance - renewal',
        'account_code': '4100',
        'line_desc': 'Motor fleet cover - 85 vehicles',
        'unit_price': D('520000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 12, 20),
    },
    # Debswana - workers compensation
    {
        'contact_name': 'Debswana Diamond Company (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2026, 1, 5),
        'description': "Workers' compensation insurance",
        'account_code': '4100',
        'line_desc': "Workers' compensation - all mining personnel",
        'unit_price': D('1450000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_partial',
        'pay_date': datetime.date(2026, 1, 30),
        'pay_amount': D('750000.00'),
    },
    # FNB - cyber liability
    {
        'contact_name': 'First National Bank Botswana (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2026, 1, 15),
        'description': 'Cyber liability insurance',
        'account_code': '4100',
        'line_desc': 'Cyber liability & data breach cover',
        'unit_price': D('780000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'overdue',
    },
    # Choppies - goods in transit
    {
        'contact_name': 'Choppies Enterprises (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2026, 2, 1),
        'description': 'Goods in transit insurance',
        'account_code': '4100',
        'line_desc': 'Goods in transit - regional distribution',
        'unit_price': D('340000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'post',
    },
    # Mascom - directors & officers
    {
        'contact_name': 'Mascom Wireless (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2026, 2, 15),
        'description': "Directors & officers liability",
        'account_code': '4100',
        'line_desc': "D&O liability cover",
        'unit_price': D('420000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'post',
    },
    # Wilderness - Q1 2026 renewal (draft)
    {
        'contact_name': 'Wilderness Safaris Botswana (Sample)',
        'invoice_type': 'customer_invoice',
        'issue_date': datetime.date(2026, 3, 10),
        'description': 'Safari lodge fire & perils - renewal quote',
        'account_code': '4100',
        'line_desc': 'Fire & special perils - 12 safari lodges',
        'unit_price': D('920000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'draft',
    },

    # ── Vendor bills (claims) ───────────────────────────────────────────
    # Hospital claim - Debswana worker injury
    {
        'contact_name': 'Gaborone Private Hospital (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2025, 9, 10),
        'description': 'Claim CLM-2025-001: Worker injury - Jwaneng mine',
        'account_code': '5100',
        'line_desc': 'Medical treatment costs - occupational injury',
        'unit_price': D('420000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 9, 25),
    },
    # Motor claim - BPC vehicle
    {
        'contact_name': 'Autobody Repairs BW (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2025, 10, 20),
        'description': 'Claim CLM-2025-002: Motor vehicle accident - BPC fleet',
        'account_code': '5100',
        'line_desc': 'Vehicle repair costs - Toyota Hilux reg B-425-AXZ',
        'unit_price': D('185000.00'),
        'tax_code': 'VAT_STD',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 11, 5),
    },
    # Property claim - Choppies store fire
    {
        'contact_name': 'Delta Property Assessors (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2025, 11, 15),
        'description': 'Claim CLM-2025-003: Fire damage assessment - Choppies Maun',
        'account_code': '5100',
        'line_desc': 'Property damage assessment & restoration estimate',
        'unit_price': D('145000.00'),
        'tax_code': 'VAT_STD',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 12, 1),
    },
    # Large hospital claim
    {
        'contact_name': 'Gaborone Private Hospital (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2025, 12, 5),
        'description': 'Claim CLM-2025-004: Mining accident - multiple injuries',
        'account_code': '5100',
        'line_desc': 'Emergency treatment & hospitalization - 3 patients',
        'unit_price': D('850000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 12, 20),
    },
    # Motor claim - fleet
    {
        'contact_name': 'Autobody Repairs BW (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 1, 10),
        'description': 'Claim CLM-2026-001: Multi-vehicle collision - BPC fleet',
        'account_code': '5100',
        'line_desc': 'Repair of 3 fleet vehicles following collision',
        'unit_price': D('520000.00'),
        'tax_code': 'VAT_STD',
        'action': 'pay_partial',
        'pay_date': datetime.date(2026, 1, 25),
        'pay_amount': D('300000.00'),
    },
    # Property assessment - FNB branch
    {
        'contact_name': 'Delta Property Assessors (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 2, 10),
        'description': 'Claim CLM-2026-002: Water damage - FNB Francistown branch',
        'account_code': '5100',
        'line_desc': 'Flood damage assessment & remediation',
        'unit_price': D('280000.00'),
        'tax_code': 'VAT_STD',
        'action': 'post',
    },
    # Recent hospital claim (overdue)
    {
        'contact_name': 'Gaborone Private Hospital (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 2, 1),
        'due_date_override': datetime.date(2026, 2, 15),
        'description': 'Claim CLM-2026-003: Workplace injury settlement',
        'account_code': '5100',
        'line_desc': 'Surgery & rehabilitation costs',
        'unit_price': D('580000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'overdue',
    },
    # New claim (draft)
    {
        'contact_name': 'Autobody Repairs BW (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 3, 15),
        'description': 'Claim CLM-2026-004: Pending vehicle theft claim',
        'account_code': '5100',
        'line_desc': 'Total loss - stolen vehicle Toyota Land Cruiser',
        'unit_price': D('720000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'draft',
    },

    # ── Broker commission bills ─────────────────────────────────────────
    # AON - Q1 FY2025/26 commission
    {
        'contact_name': 'AON Botswana (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2025, 10, 1),
        'description': 'Broker commission - Q1 FY2025/26 (Jul-Sep 2025)',
        'account_code': '5400',
        'line_desc': 'Commission on placed premiums - Q1',
        'unit_price': D('245000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 10, 28),
    },
    # Marsh - Q1 FY2025/26 commission
    {
        'contact_name': 'Marsh Botswana (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2025, 10, 1),
        'description': 'Broker commission - Q1 FY2025/26 (Jul-Sep 2025)',
        'account_code': '5400',
        'line_desc': 'Commission on placed premiums - Q1',
        'unit_price': D('180000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2025, 10, 30),
    },
    # Glenrand - Q2 commission
    {
        'contact_name': 'Glenrand MIB Botswana (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 1, 5),
        'description': 'Broker commission - Q2 FY2025/26 (Oct-Dec 2025)',
        'account_code': '5400',
        'line_desc': 'Commission on placed premiums - Q2',
        'unit_price': D('198000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'pay_full',
        'pay_date': datetime.date(2026, 1, 28),
    },
    # AON - Q2 commission
    {
        'contact_name': 'AON Botswana (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 1, 10),
        'description': 'Broker commission - Q2 FY2025/26 (Oct-Dec 2025)',
        'account_code': '5400',
        'line_desc': 'Commission on placed premiums - Q2',
        'unit_price': D('265000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'post',
    },
    # Marsh - Q2 commission (overdue)
    {
        'contact_name': 'Marsh Botswana (Sample)',
        'invoice_type': 'vendor_bill',
        'issue_date': datetime.date(2026, 1, 15),
        'due_date_override': datetime.date(2026, 2, 14),
        'description': 'Broker commission - Q2 FY2025/26 (Oct-Dec 2025)',
        'account_code': '5400',
        'line_desc': 'Commission on placed premiums - Q2',
        'unit_price': D('195000.00'),
        'tax_code': 'VAT_ZERO',
        'action': 'overdue',
    },
]


class Command(BaseCommand):
    help = (
        'Create realistic sample data for Alpha Direct Insurance demo. '
        'Populates contacts, invoices, and payments for fiscal year 2025/26.'
    )

    def handle(self, *args, **options):
        # ── Idempotency check ───────────────────────────────────────────
        if Contact.objects.filter(name=SENTINEL_NAME).exists():
            self.stdout.write(self.style.WARNING(
                'Sample data already exists (sentinel contact found). '
                'Skipping creation. Delete existing sample contacts to re-run.'
            ))
            return

        # ── Prerequisites ───────────────────────────────────────────────
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stderr.write(self.style.ERROR(
                'No superuser found. Run setup_initial_data first.'
            ))
            return

        try:
            bwp = Currency.objects.get(pk='BWP')
        except Currency.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                'BWP currency not found. Run setup_initial_data first.'
            ))
            return

        # Validate required accounts exist
        required_accounts = ['1110', '1210', '2130', '2140', '4100', '5100', '5400']
        for code in required_accounts:
            if not Account.objects.filter(code=code).exists():
                self.stderr.write(self.style.ERROR(
                    f'Account {code} not found. Run setup_chart_of_accounts first.'
                ))
                return

        # Validate tax codes
        tax_codes = {}
        for code in ['VAT_STD', 'VAT_ZERO', 'VAT_EXEMPT']:
            tc = TaxRate.objects.filter(tax_code=code).first()
            if not tc:
                self.stderr.write(self.style.ERROR(
                    f'Tax code {code} not found. Run setup_initial_data first.'
                ))
                return
            tax_codes[code] = tc

        fnb_bwp = Account.objects.get(code='1110')

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\nCreating Alpha Direct sample data for FY 2025/26...'
        ))

        # ── Create contacts ─────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Contacts:'))
        contacts = {}
        for cdata in CONTACTS:
            name = cdata['name']
            contact, created = Contact.objects.get_or_create(
                name=name,
                defaults={
                    'contact_type': cdata['contact_type'],
                    'registration_number': cdata.get('registration_number', ''),
                    'tax_id': cdata.get('tax_id', ''),
                    'email': cdata.get('email', ''),
                    'phone': cdata.get('phone', ''),
                    'address': cdata.get('address', ''),
                    'currency_code': bwp,
                    'payment_terms_days': cdata.get('payment_terms_days', 30),
                    'is_resident': True,
                    'wht_exempt': cdata.get('wht_exempt', cdata['contact_type'] != 'broker'),
                },
            )
            contacts[name] = contact
            status_label = self.style.SUCCESS('created') if created else 'exists'
            self.stdout.write(
                f'    {name} ({cdata["contact_type"]}): {status_label}'
            )

        # ── Create invoices and payments ────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Invoices:'))

        invoices_created = 0
        invoices_posted = 0
        payments_created = 0
        total_gwp = ZERO
        total_claims = ZERO
        total_commission = ZERO
        total_cash_in = ZERO
        total_cash_out = ZERO

        for inv_data in INVOICES:
            contact = contacts[inv_data['contact_name']]
            acct = Account.objects.get(code=inv_data['account_code'])
            tc = tax_codes[inv_data['tax_code']]
            action = inv_data['action']

            # Create invoice
            inv = Invoice(
                invoice_type=inv_data['invoice_type'],
                contact=contact,
                issue_date=inv_data['issue_date'],
                currency_code=bwp,
                description=inv_data['description'],
                created_by=admin_user,
            )
            # Set due_date override if provided
            if 'due_date_override' in inv_data:
                inv.due_date = inv_data['due_date_override']

            inv.save(audit_user=admin_user)

            # Create invoice line
            InvoiceLine.objects.create(
                invoice=inv,
                account=acct,
                description=inv_data['line_desc'],
                quantity=D('1.0000'),
                unit_price=inv_data['unit_price'],
                tax_code=tc,
            )

            # Recalculate totals
            inv.recalculate_totals()
            inv.save(audit_user=admin_user)
            invoices_created += 1

            # Track GWP / claims / commission
            if inv_data['account_code'] == '4100':
                total_gwp += inv_data['unit_price']
            elif inv_data['account_code'] == '5100':
                total_claims += inv_data['unit_price']
            elif inv_data['account_code'] == '5400':
                total_commission += inv_data['unit_price']

            action_label = 'DRAFT'

            if action != 'draft':
                # Post the invoice
                inv.post(user=admin_user)
                invoices_posted += 1
                action_label = 'POSTED'

                if action == 'overdue':
                    # Mark as overdue via direct DB update (since posted invoices
                    # are immutable through normal save)
                    Invoice.objects.filter(pk=inv.pk).update(
                        status=Invoice.Status.OVERDUE,
                    )
                    action_label = 'OVERDUE'

                elif action in ('pay_full', 'pay_partial'):
                    # Determine payment details
                    if action == 'pay_full':
                        pay_amount = inv.total_amount
                    else:
                        pay_amount = inv_data['pay_amount']

                    pay_date = inv_data['pay_date']

                    # Determine payment type/direction
                    if inv_data['invoice_type'] == 'customer_invoice':
                        pay_type = Payment.PaymentType.RECEIVED
                        total_cash_in += pay_amount
                    else:
                        pay_type = Payment.PaymentType.SENT
                        total_cash_out += pay_amount

                    pay = Payment(
                        payment_type=pay_type,
                        contact=contact,
                        bank_account=fnb_bwp,
                        payment_date=pay_date,
                        currency_code=bwp,
                        exchange_rate=D('1.00000000'),
                        amount=pay_amount,
                        payment_method=Payment.PaymentMethod.BANK_TRANSFER,
                        reference=f'REF-SAMPLE-{inv.invoice_number}',
                        description=f'Payment for {inv.invoice_number}',
                        created_by=admin_user,
                    )
                    pay.save(audit_user=admin_user)

                    # Allocate payment to invoice
                    PaymentAllocation.objects.create(
                        payment=pay,
                        invoice=inv,
                        amount_allocated=pay_amount,
                    )

                    # Confirm the payment (creates JE, applies WHT for brokers)
                    pay.confirm(user=admin_user, _system=True)
                    payments_created += 1

                    if action == 'pay_full':
                        action_label = 'PAID'
                    else:
                        action_label = f'PARTIAL ({pay_amount})'

            self.stdout.write(
                f'    {inv.invoice_number}  '
                f'{inv_data["invoice_type"]:<20}  '
                f'P{inv.total_amount:>12,.2f}  '
                f'{action_label}'
            )

        # ── Summary ─────────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n  Summary:'))
        self.stdout.write(f'    Contacts created:       {len(CONTACTS)}')
        self.stdout.write(f'    Invoices created:       {invoices_created}')
        self.stdout.write(f'    Invoices posted:        {invoices_posted}')
        self.stdout.write(f'    Payments created:       {payments_created}')
        self.stdout.write(f'    ')
        self.stdout.write(f'    Gross Written Premium:  P{total_gwp:>12,.2f}')
        self.stdout.write(f'    Claims incurred:        P{total_claims:>12,.2f}')
        self.stdout.write(f'    Broker commissions:     P{total_commission:>12,.2f}')
        self.stdout.write(f'    Cash received:          P{total_cash_in:>12,.2f}')
        self.stdout.write(f'    Cash paid out:          P{total_cash_out:>12,.2f}')
        self.stdout.write(f'    Net cash position:      P{(total_cash_in - total_cash_out):>12,.2f}')

        self.stdout.write(self.style.SUCCESS(
            '\n  Sample data created successfully!'
        ))
