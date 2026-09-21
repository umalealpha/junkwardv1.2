"""
Keetile's "error code 500" on creating a petty cash reimbursement, 12 August 2026.

The cause was one token. `reimbursement_date` (and `voucher_date`) were declared as

    models.DateField(default=timezone.now)

and `timezone.now` returns a *datetime*, not a *date*. Postgres happily casts the
datetime down to a date on the way into a `date` column, so the row saved and the
record appeared in the list. But the instance still held in memory the datetime the
default produced — `.objects.create()` does not re-read the row — and the very next
line of the API is:

    return Response(PettyCashReimbursementDetailSerializer(reimb).data, status=201)

DRF's DateField refuses to serialise a datetime ("Expected a `date`, but got a
`datetime`. Refusing to coerce, as this may mean losing timezone information."), so
the request died with a 500 *after* the write had committed.

That combination is the dangerous part, and why these tests exist: the user is told
the operation failed when it actually succeeded, so the natural response is to press
the button again and create a duplicate.

The fix is `default=timezone.localdate`, which returns a `date` in the project's
Africa/Gaborone timezone — not `timezone.now().date()`, which would give the UTC day
and so roll over two hours early every night.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from petty_cash import services
from petty_cash.models import PettyCashReimbursement, PettyCashVoucher
from petty_cash.serializers import (
    PettyCashReimbursementDetailSerializer,
    PettyCashVoucherListSerializer,
)

User = get_user_model()


class DateDefaultIsADateTests(TestCase):
    """The defaults themselves. A datetime here is what caused the 500."""

    def test_reimbursement_date_default_is_a_plain_date(self):
        value = PettyCashReimbursement._meta.get_field('reimbursement_date').get_default()
        self.assertIsInstance(value, dt.date)
        self.assertNotIsInstance(
            value, dt.datetime,
            'reimbursement_date must default to a date, not a datetime — a datetime '
            'makes the DRF DateField assert and returns 500 after the row is saved.',
        )

    def test_voucher_date_default_is_a_plain_date(self):
        value = PettyCashVoucher._meta.get_field('voucher_date').get_default()
        self.assertIsInstance(value, dt.date)
        self.assertNotIsInstance(
            value, dt.datetime,
            'voucher_date must default to a date, not a datetime.',
        )


class SerialisingAFreshlyCreatedRecordTests(TestCase):
    """The actual 500, reproduced: create, then serialise WITHOUT reloading."""

    def setUp(self):
        from core.models import Company
        from ledger.models import Account, Currency
        from petty_cash.models import PettyCashLocation

        bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        company, _ = Company.objects.get_or_create(
            code='PCDT', defaults={'name': 'Petty Cash Date Test', 'base_currency': bwp})
        self.expense, _ = Account.objects.get_or_create(
            code='PC-EXP-DT', defaults={'name': 'Refreshments', 'account_type': 'expense',
                                        'currency_code': bwp})
        float_acc, _ = Account.objects.get_or_create(
            code='PC-FLOAT-DT', defaults={'name': 'Float', 'account_type': 'asset',
                                          'currency_code': bwp})
        bank, _ = Account.objects.get_or_create(
            code='PC-BANK-DT', defaults={'name': 'Bank', 'account_type': 'asset',
                                         'currency_code': bwp, 'is_bank_account': True})
        self.location = PettyCashLocation.objects.create(
            company=company, name='Date Test Office', float_amount=Decimal('5000.00'),
            petty_cash_account=float_acc, reimbursing_bank_account=bank)
        self.user = User.objects.create_user(
            'pcdate-custodian', password='x', email='pcdate@example.co.bw', is_superuser=True)

        # One POSTED, unswept voucher so there is something to reimburse
        # (that is what _eligible_vouchers looks for).
        self.voucher = PettyCashVoucher.objects.create(
            location=self.location, voucher_date=dt.date(2026, 8, 5), payee='Shop',
            amount=Decimal('25.00'), expense_account=self.expense,
            description='Cash to buy refreshments',
            status=PettyCashVoucher.Status.POSTED,
            submitted_by=self.user)

    def test_creating_a_reimbursement_can_be_serialised(self):
        """This is Keetile's exact request path: create_reimbursement then serialise."""
        reimb = services.create_reimbursement(
            location=self.location,
            period_start=dt.date(2026, 8, 1),
            period_end=dt.date(2026, 8, 31),
            user=self.user,
        )
        # No refresh_from_db() on purpose — the API does not do one either, and that
        # is precisely why the bug was invisible everywhere except on create.
        self.assertNotIsInstance(reimb.reimbursement_date, dt.datetime)
        data = PettyCashReimbursementDetailSerializer(reimb).data   # used to raise AssertionError
        self.assertEqual(data['reimbursement_date'], str(reimb.reimbursement_date))

    def test_creating_a_voucher_can_be_serialised(self):
        """Same latent fault on the voucher, which is created far more often."""
        voucher = PettyCashVoucher.objects.create(
            location=self.location, payee='Kiosk', amount=Decimal('40.00'),
            expense_account=self.expense, description='Milk and sugar',
            submitted_by=self.user)
        self.assertNotIsInstance(voucher.voucher_date, dt.datetime)
        data = PettyCashVoucherListSerializer(voucher).data          # used to raise AssertionError
        self.assertEqual(data['voucher_date'], str(voucher.voucher_date))

    def test_the_date_is_the_botswana_day_not_the_utc_day(self):
        """timezone.now().date() would roll over at 22:00 local. localdate does not."""
        from django.utils import timezone
        voucher = PettyCashVoucher.objects.create(
            location=self.location, payee='Kiosk', amount=Decimal('10.00'),
            expense_account=self.expense, description='Sugar',
            submitted_by=self.user)
        self.assertEqual(voucher.voucher_date, timezone.localdate())
