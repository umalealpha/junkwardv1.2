"""Bug 0329c5a0 (2026-08-20): the Team Leave Report and the Leave Encashment
module do not agree.

The report: "when an employee applies for leave and it is approved and marked
as paid in Leave Encashment, the corresponding days are not deducted from the
employee's leave balance in Leave Admin ... employees cannot over-claim leave."

That is correct, and the reason the same encashment looked right on one screen
and missing on another is that there are two derivations. `hris/leave_balance.py`
(the employee's own balance, and the card at the bottom of /hris/leave) has
reserved encashed days since 2026-07-21 — `available = accrued − used −
encashed`. `leave_report` (the Team Leave Report HR works from) computes
`opening + accrued − taken` and has never had an encashment term at all.

Measured on prod, 20 Aug 2026: the HR report over-stated five employees by
exactly their encashed days — 53.0 days in total, two of them already PAID.
Anonymised, the shape was identical on every row:

    encashment  days   report closing   own balance   gap
    approved     8.0         17.50          9.50      8.00
    paid         6.0         16.00         10.00      6.00
    pending     20.0         44.19         24.19     20.00
    paid        11.0         24.00         13.00     11.00
    approved     8.0         21.00         13.00      8.00

The gap equals the encashment on every row. That is the whole bug.
(Named figures live in the fabe ledger, not in the repo.)
"""
import datetime as _dt
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company
from hris.models import HRISProfile, LeaveOpeningBalance
from payroll.models import Employee


def _report(user, year, leave_type='annual'):
    from hris.feature_views import leave_report
    req = APIRequestFactory().get('/hris/api/leave-report/', {
        'date_from': f'{year}-01-01', 'date_to': f'{year}-12-31',
        'leave_type': leave_type})
    force_authenticate(req, user=user)
    return leave_report(req)


class LeaveReportEncashmentTests(TestCase):
    """The HR report must deduct encashed days, and must agree with the
    employee's own balance. A gap between the two screens IS the defect."""

    _seq = 0

    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC',
                                                  name='Alpha Direct Insurance'))
        User = get_user_model()
        self.hr = User.objects.create_superuser(
            'hr-encash-report', 'hr-encash@example.invalid', 'x')
        from django.utils import timezone
        self.today = timezone.now().date()
        # Anchor at a month end five months back so whole months have completed
        # however far into the month the suite runs (pattern copied from
        # test_leave_accrual_oprah_spotcheck.py).
        m, y = self.today.month - 5, self.today.year
        while m < 1:
            m += 12
            y -= 1
        first = _dt.date(y, m, 1)
        self.as_at = (first.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) \
            - _dt.timedelta(days=1)

    # -- helpers ----------------------------------------------------------

    def _staff(self, name, *, entitlement=21, opening='20.00', anchored=True):
        LeaveReportEncashmentTests._seq += 1
        emp = Employee.objects.create(
            full_name=name, company=self.company,
            employee_number=f'ENCRPT{LeaveReportEncashmentTests._seq:03d}')
        prof = HRISProfile.objects.create(employee=emp)
        if anchored:
            LeaveOpeningBalance.objects.create(
                profile=prof, leave_type_code='annual', as_at_date=self.as_at,
                entitlement_days=D(str(entitlement)),
                opening_balance_days=D(opening), accrued_days=D(opening))
        return prof

    def _encash(self, profile, days, status, *, created=None, code='annual'):
        from hris.leave_encash_models import LeaveEncashment
        e = LeaveEncashment.objects.create(
            employee=profile.employee, profile=profile, company=self.company,
            leave_type_code=code, days=D(str(days)), status=status)
        if created is not None:
            LeaveEncashment.objects.filter(pk=e.pk).update(created_at=created)
            e.refresh_from_db()
        return e

    def _row(self, name, year=None):
        resp = _report(self.hr, year or self.today.year)
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        return next((r for r in resp.data['rows'] if r['employee_name'] == name), None)

    # -- the reported defect ----------------------------------------------

    def test_a_paid_encashment_is_deducted_from_the_hr_report(self):
        """Her exact scenario: approved AND marked paid."""
        from hris.leave_encash_models import LeaveEncashment as LE
        prof = self._staff('Paid Encashment Person')
        before = self._row('Paid Encashment Person')['closing_balance']

        self._encash(prof, 11, LE.Status.PAID)
        after = self._row('Paid Encashment Person')

        self.assertEqual(after['encashed'], 11.0)
        self.assertAlmostEqual(after['closing_balance'], before - 11.0, places=2)

    def test_the_hr_report_agrees_with_the_employees_own_balance(self):
        """The two screens disagreeing is the bug. Pin them together."""
        from hris.leave_encash_models import LeaveEncashment as LE
        from hris.leave_balance import balances_for_profile
        prof = self._staff('Two Screens Person')
        self._encash(prof, 6, LE.Status.PAID)

        row = self._row('Two Screens Person')
        own = next(b for b in balances_for_profile(prof) if b['code'] == 'annual')

        self.assertEqual(row['encashed'], own['encashed'])
        self.assertAlmostEqual(row['closing_balance'], own['available'], places=2)

    def test_an_approved_but_unpaid_encashment_is_already_reserved(self):
        """Days must not be re-spendable while the payment is in flight."""
        from hris.leave_encash_models import LeaveEncashment as LE
        prof = self._staff('Approved Encashment Person')
        before = self._row('Approved Encashment Person')['closing_balance']

        self._encash(prof, 8, LE.Status.APPROVED)

        self.assertAlmostEqual(
            self._row('Approved Encashment Person')['closing_balance'],
            before - 8.0, places=2)

    def test_a_rejected_encashment_releases_the_days(self):
        from hris.leave_encash_models import LeaveEncashment as LE
        prof = self._staff('Rejected Encashment Person')
        before = self._row('Rejected Encashment Person')['closing_balance']

        self._encash(prof, 9, LE.Status.REJECTED)

        self.assertAlmostEqual(
            self._row('Rejected Encashment Person')['closing_balance'],
            before, places=2)
        self.assertEqual(self._row('Rejected Encashment Person')['encashed'], 0.0)

    def test_an_encashment_predating_the_uploaded_anchor_is_not_double_counted(self):
        """HR's uploaded opening balance already has older encashments baked in
        — subtracting them again would rob the employee twice. Same as_at rule
        as leave_balance.py."""
        from hris.leave_encash_models import LeaveEncashment as LE
        from django.utils import timezone
        prof = self._staff('Pre Anchor Person')
        before = self._row('Pre Anchor Person')['closing_balance']

        stamp = timezone.make_aware(
            _dt.datetime.combine(self.as_at - _dt.timedelta(days=10),
                                 _dt.time(9, 0)))
        self._encash(prof, 7, LE.Status.PAID, created=stamp)

        self.assertAlmostEqual(self._row('Pre Anchor Person')['closing_balance'],
                               before, places=2)

    def test_an_encashment_after_the_anchor_is_deducted(self):
        """The other half of the same rule — otherwise nothing is ever deducted
        for the ~90 uploaded employees, which is everyone."""
        from hris.leave_encash_models import LeaveEncashment as LE
        prof = self._staff('Post Anchor Person')
        before = self._row('Post Anchor Person')['closing_balance']

        self._encash(prof, 5, LE.Status.PAID)

        self.assertAlmostEqual(self._row('Post Anchor Person')['closing_balance'],
                               before - 5.0, places=2)

    def test_an_unanchored_employee_is_deducted_too(self):
        from hris.leave_encash_models import LeaveEncashment as LE
        prof = self._staff('No Upload Person', anchored=False)
        before = self._row('No Upload Person')['closing_balance']

        self._encash(prof, 4, LE.Status.PAID)

        self.assertAlmostEqual(self._row('No Upload Person')['closing_balance'],
                               before - 4.0, places=2)

    def test_an_encashment_does_not_leak_across_leave_types(self):
        from hris.leave_encash_models import LeaveEncashment as LE
        prof = self._staff('Wrong Type Person')
        before = self._row('Wrong Type Person')['closing_balance']

        self._encash(prof, 5, LE.Status.PAID, code='sick')

        self.assertAlmostEqual(self._row('Wrong Type Person')['closing_balance'],
                               before, places=2)

    def test_one_persons_encashment_does_not_touch_another(self):
        from hris.leave_encash_models import LeaveEncashment as LE
        a = self._staff('Encasher A')
        self._staff('Bystander B')
        before_b = self._row('Bystander B')['closing_balance']

        self._encash(a, 10, LE.Status.PAID)

        self.assertAlmostEqual(self._row('Bystander B')['closing_balance'],
                               before_b, places=2)
        self.assertEqual(self._row('Bystander B')['encashed'], 0.0)
