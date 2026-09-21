"""
Management command: create_test_payments

Creates and confirms two test payments to verify JE generation, WHT, and
invoice allocation logic.

Test 1 — Payment received from customer:
  PAY-IN  P9,120 from Alpha Motors  ->  allocated to INV-2026-000002
  Expected JE:
    Dr  1110  FNB BWP operating account   9,120.00
    Cr  1210  Premium receivable          9,120.00
  Expected: Invoice status -> PAID

Test 2 — Payment sent to broker (with WHT):
  PAY-OUT  P10,000 gross to Sure Brokers
  Expected JE:
    Dr  2130  Broker commission payable  10,000.00
    Cr  1110  FNB BWP operating account   9,000.00
    Cr  2170  WHT payable                 1,000.00
  Expected: WHT record created (rate=10%, wht_amount=P1,000, net=P9,000)
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import Contact, Invoice
from ledger.models import Account
from ledger.views import _build_trial_balance
from payments.models import Payment, PaymentAllocation, WithholdingTaxRecord


class Command(BaseCommand):
    help = 'Create and confirm test payments, then print JEs and trial balance.'

    def handle(self, *args, **options):
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stderr.write('No superuser found. Run setup_initial_data first.')
            return

        today    = timezone.localdate()
        fnb_bwp  = Account.objects.get(code='1110')

        self.stdout.write(self.style.MIGRATE_HEADING('\nTest 1 — Payment received from customer'))
        pay1 = self._confirm_customer_payment(admin_user, today, fnb_bwp)

        self.stdout.write(self.style.MIGRATE_HEADING('\nTest 2 — Payment sent to broker (with WHT)'))
        pay2 = self._confirm_broker_payment(admin_user, today, fnb_bwp)

        # Print JEs
        self.stdout.write(self.style.MIGRATE_HEADING('\nJournal Entries:'))
        for pay in filter(None, [pay1, pay2]):
            if pay.journal_entry:
                self._print_je(pay)

        # WHT summary
        wht_records = WithholdingTaxRecord.objects.select_related(
            'contact', 'payment'
        ).all()
        if wht_records.exists():
            self.stdout.write(self.style.MIGRATE_HEADING('\nWithholding Tax Records:'))
            for r in wht_records:
                self.stdout.write(
                    f"  {r.payment.payment_number}  contact={r.contact.name}"
                )
                self.stdout.write(
                    f"    gross={r.gross_amount}  rate={r.wht_rate}%"
                    f"  wht={r.wht_amount}  net={r.net_amount}"
                )
                self.stdout.write(
                    f"    tax_year_start={r.tax_year_start}"
                    f"  cumulative_ytd={r.cumulative_paid_ytd}"
                )

        # Trial balance
        self.stdout.write(self.style.MIGRATE_HEADING('\nFull Trial Balance (all posted entries):'))
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
        style  = self.style.SUCCESS if t['balanced'] else self.style.ERROR
        self.stdout.write(f"\n  {style('BALANCED' if t['balanced'] else 'NOT BALANCED')}")

    # ------------------------------------------------------------------
    # Test 1 — customer payment
    # ------------------------------------------------------------------

    def _confirm_customer_payment(self, user, today, bank_account):
        customer = Contact.objects.filter(name='Alpha Motors (Test)').first()
        if not customer:
            self.stderr.write('  Alpha Motors (Test) not found. Run create_test_invoices first.')
            return None

        # Find the posted customer invoice
        invoice = Invoice.objects.filter(
            contact=customer,
            invoice_type='customer_invoice',
            status__in=[Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID],
        ).first()
        if not invoice:
            self.stderr.write(f'  No open invoice for {customer.name}.')
            return None

        # Idempotency: skip if already paid
        if invoice.status == Invoice.Status.PAID:
            self.stdout.write(
                f'  Skipped — {invoice.invoice_number} already paid.'
            )
            existing = Payment.objects.filter(
                contact=customer, payment_type='received',
                status=Payment.Status.CONFIRMED,
            ).first()
            return existing

        pay = Payment(
            payment_type   = Payment.PaymentType.RECEIVED,
            contact        = customer,
            bank_account   = bank_account,
            payment_date   = today,
            currency_code  = invoice.currency_code,
            exchange_rate  = Decimal('1.00000000'),
            amount         = invoice.total_amount,
            payment_method = Payment.PaymentMethod.BANK_TRANSFER,
            reference      = f'REF-TEST-CUST-001',
            description    = f'Payment for {invoice.invoice_number}',
            created_by     = user,
        )
        pay.save(audit_user=user)
        self.stdout.write(f'  Payment created: {pay.payment_number}  amount={pay.amount}')

        # Allocate full amount to the invoice
        PaymentAllocation.objects.create(
            payment=pay, invoice=invoice, amount_allocated=invoice.total_amount,
        )
        self.stdout.write(
            f'  Allocated {invoice.total_amount} -> {invoice.invoice_number}'
        )

        pay.confirm(user=user)
        self.stdout.write(
            self.style.SUCCESS(f'  Confirmed: {pay.payment_number}  JE={pay.journal_entry.entry_number}')
        )

        # Refresh invoice from DB
        invoice.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f'  Invoice {invoice.invoice_number} status: {invoice.status}'
                f'  amount_paid={invoice.amount_paid}  balance_due={invoice.balance_due}'
            )
        )
        return pay

    # ------------------------------------------------------------------
    # Test 2 — broker payment with WHT
    # ------------------------------------------------------------------

    def _confirm_broker_payment(self, user, today, bank_account):
        broker = Contact.objects.filter(name='Sure Brokers (Test)').first()
        if not broker:
            self.stderr.write('  Sure Brokers (Test) not found. Run create_test_invoices first.')
            return None

        # Idempotency
        existing = Payment.objects.filter(
            contact=broker,
            payment_type=Payment.PaymentType.SENT,
            status=Payment.Status.CONFIRMED,
        ).first()
        if existing:
            self.stdout.write(f'  Skipped — {existing.payment_number} already confirmed.')
            return existing

        pay = Payment(
            payment_type   = Payment.PaymentType.SENT,
            contact        = broker,
            bank_account   = bank_account,
            payment_date   = today,
            currency_code_id = 'BWP',
            exchange_rate  = Decimal('1.00000000'),
            amount         = Decimal('10000.00'),
            payment_method = Payment.PaymentMethod.BANK_TRANSFER,
            reference      = 'REF-TEST-BROKER-001',
            description    = 'Broker commission payment (test)',
            created_by     = user,
        )
        pay.save(audit_user=user)
        self.stdout.write(f'  Payment created: {pay.payment_number}  gross={pay.amount}')

        pay.confirm(user=user)
        self.stdout.write(
            self.style.SUCCESS(
                f'  Confirmed: {pay.payment_number}  JE={pay.journal_entry.entry_number}'
            )
        )

        # WHT record
        try:
            wht = pay.wht_record
            self.stdout.write(
                self.style.SUCCESS(
                    f'  WHT: gross={wht.gross_amount}  '
                    f'wht={wht.wht_amount} ({wht.wht_rate}%)  '
                    f'net={wht.net_amount}'
                )
            )
        except WithholdingTaxRecord.DoesNotExist:
            self.stdout.write(self.style.ERROR('  WHT record NOT created (unexpected).'))

        return pay

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _print_je(self, payment):
        je = payment.journal_entry
        self.stdout.write(f'\n  {je.entry_number} — {je.description}')
        self.stdout.write(f"  {'Account':<40} {'Dr BWP':>12} {'Cr BWP':>12}")
        self.stdout.write('  ' + '-' * 66)
        for line in je.lines.select_related('account').all():
            dr = str(line.debit_bwp)  if line.debit_bwp  else ''
            cr = str(line.credit_bwp) if line.credit_bwp else ''
            self.stdout.write(f"  {str(line.account):<40} {dr:>12} {cr:>12}")
        total_dr = sum(ln.debit_bwp  for ln in je.lines.all())
        total_cr = sum(ln.credit_bwp for ln in je.lines.all())
        self.stdout.write('  ' + '-' * 66)
        self.stdout.write(f"  {'TOTAL':<40} {str(total_dr):>12} {str(total_cr):>12}")
        ok = total_dr == total_cr
        self.stdout.write(
            '  ' + (self.style.SUCCESS('Balanced') if ok else self.style.ERROR('NOT balanced'))
        )
