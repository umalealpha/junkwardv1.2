"""
Management command: create_test_invoices

Creates and posts three test invoices to verify automatic journal entry generation:

  1. Customer invoice  — Motor vehicle premium P8,000 + VAT 14% = P9,120
     Expected JE:
       Dr  1210  Premium receivable            9,120.00
       Cr  4100  Gross written premium          8,000.00
       Cr  2180  VAT output payable             1,120.00

  2. Vendor bill       — Office supplies P500 + VAT 14% = P570
     Expected JE:
       Dr  6220  Office supplies                  500.00
       Dr  1250  VAT input receivable               70.00
       Cr  2140  Vendor payable                   570.00

  3. Broker commission bill — Commission P10,000, no VAT
     Expected JE:
       Dr  5400  Commission expense            10,000.00
       Cr  2130  Broker commission payable     10,000.00
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import Contact, Invoice, InvoiceLine
from core.models import Currency, TaxRate
from ledger.views import _build_trial_balance


class Command(BaseCommand):
    help = 'Create and post test invoices, then print JEs and trial balance.'

    def handle(self, *args, **options):
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stderr.write('No superuser found. Run setup_initial_data first.')
            return

        bwp      = Currency.objects.get(pk='BWP')
        vat_std  = TaxRate.objects.get(tax_code='VAT_STD')
        vat_zero = TaxRate.objects.get(tax_code='VAT_ZERO')
        today    = timezone.localdate()

        self.stdout.write(self.style.MIGRATE_HEADING('\nCreating test contacts...'))
        customer = self._make_contact('Alpha Motors (Test)',  'customer',  bwp)
        vendor   = self._make_contact('Office Depot BW (Test)', 'vendor', bwp)
        broker   = self._make_contact('Sure Brokers (Test)',  'broker',    bwp)

        self.stdout.write(self.style.MIGRATE_HEADING('\nCreating and posting invoices...'))

        inv1 = self._post_invoice(
            label       = '1. Customer invoice — Motor vehicle premium',
            inv_type    = 'customer_invoice',
            contact     = customer,
            issue_date  = today,
            user        = admin_user,
            lines       = [
                {
                    'account_code': '4100',
                    'description':  'Motor vehicle premium',
                    'quantity':     Decimal('1.0000'),
                    'unit_price':   Decimal('8000.00'),
                    'tax_code':     vat_std,
                },
            ],
        )

        inv2 = self._post_invoice(
            label       = '2. Vendor bill — Office supplies',
            inv_type    = 'vendor_bill',
            contact     = vendor,
            issue_date  = today,
            user        = admin_user,
            lines       = [
                {
                    'account_code': '6220',
                    'description':  'Office supplies',
                    'quantity':     Decimal('1.0000'),
                    'unit_price':   Decimal('500.00'),
                    'tax_code':     vat_std,
                },
            ],
        )

        inv3 = self._post_invoice(
            label       = '3. Broker bill — Commission',
            inv_type    = 'vendor_bill',
            contact     = broker,
            issue_date  = today,
            user        = admin_user,
            lines       = [
                {
                    'account_code': '5400',
                    'description':  'Broker commission — motor vehicle',
                    'quantity':     Decimal('1.0000'),
                    'unit_price':   Decimal('10000.00'),
                    'tax_code':     vat_zero,
                },
            ],
        )

        # Print JEs
        self.stdout.write(self.style.MIGRATE_HEADING('\nJournal Entries Created:'))
        for inv in (inv1, inv2, inv3):
            if inv and inv.journal_entry:
                self._print_je(inv)

        # Trial balance
        self.stdout.write(self.style.MIGRATE_HEADING('\nTrial Balance (all posted entries):'))
        tb = _build_trial_balance(end_date=today)
        self.stdout.write(
            f"  {'Code':<8} {'Account':<40} {'Dr BWP':>12} {'Cr BWP':>12} {'Balance':>12}"
        )
        self.stdout.write('  ' + '-' * 88)
        for row in tb['accounts']:
            self.stdout.write(
                f"  {row['code']:<8} {row['name']:<40} "
                f"{row['total_debits']:>12} {row['total_credits']:>12} "
                f"{row['closing_balance']:>12}"
            )
        t = tb['totals']
        self.stdout.write('  ' + '-' * 88)
        self.stdout.write(
            f"  {'TOTALS':<49} {t['total_debits']:>12} {t['total_credits']:>12}"
        )
        status = 'BALANCED' if t['balanced'] else 'NOT BALANCED'
        style  = self.style.SUCCESS if t['balanced'] else self.style.ERROR
        self.stdout.write(f"\n  {style(status)}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_contact(self, name, contact_type, currency):
        obj, created = Contact.objects.get_or_create(
            name=name,
            defaults={
                'contact_type':       contact_type,
                'currency_code':      currency,
                'payment_terms_days': 30,
            },
        )
        label = self.style.SUCCESS('created') if created else 'already exists'
        self.stdout.write(f"  {name}: {label}")
        return obj

    def _post_invoice(self, label, inv_type, contact, issue_date, user, lines):
        from ledger.models import Account
        self.stdout.write(f"\n  {label}")

        # Skip if this contact already has a posted invoice of this type
        existing = Invoice.objects.filter(
            contact=contact,
            invoice_type=inv_type,
            status=Invoice.Status.POSTED,
        ).first()
        if existing:
            self.stdout.write(
                f"    Skipped — {existing.invoice_number} already posted."
            )
            return existing

        inv = Invoice(
            invoice_type  = inv_type,
            contact       = contact,
            issue_date    = issue_date,
            currency_code = contact.currency_code,
            created_by    = user,
        )
        inv.save(audit_user=user)

        for ln_data in lines:
            acct = Account.objects.get(code=ln_data['account_code'])
            InvoiceLine.objects.create(
                invoice     = inv,
                account     = acct,
                description = ln_data['description'],
                quantity    = ln_data['quantity'],
                unit_price  = ln_data['unit_price'],
                tax_code    = ln_data['tax_code'],
            )

        inv.post(user=user)
        self.stdout.write(
            self.style.SUCCESS(
                f"    Posted {inv.invoice_number}  "
                f"total={inv.total_amount}  JE={inv.journal_entry.entry_number}"
            )
        )
        return inv

    def _print_je(self, invoice):
        je = invoice.journal_entry
        self.stdout.write(f"\n  {je.entry_number} — {je.description}")
        self.stdout.write(f"  {'Account':<40} {'Dr':>12} {'Cr':>12}")
        self.stdout.write('  ' + '-' * 66)
        for line in je.lines.select_related('account').all():
            dr = str(line.debit_bwp)  if line.debit_bwp  else ''
            cr = str(line.credit_bwp) if line.credit_bwp else ''
            self.stdout.write(
                f"  {str(line.account):<40} {dr:>12} {cr:>12}"
            )
        total_dr = sum(ln.debit_bwp  for ln in je.lines.all())
        total_cr = sum(ln.credit_bwp for ln in je.lines.all())
        self.stdout.write('  ' + '-' * 66)
        self.stdout.write(
            f"  {'TOTAL':<40} {str(total_dr):>12} {str(total_cr):>12}"
        )
        balanced = total_dr == total_cr
        self.stdout.write(
            '  ' + (self.style.SUCCESS('Balanced') if balanced else self.style.ERROR('NOT balanced'))
        )
