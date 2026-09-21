"""
hris/tests/test_amendment_reversal.py

Manus nine-area retest P2 (2026-08-25): an APPLIED HRIS amendment had no
supported way back — undoing one meant a hand-typed amendment with no link to the
original, or a direct database edit.

The design under test: a reversal is an ORDINARY amendment (old/new swapped) that
goes through the SAME maker-checker approval, so one person cannot unwind a
dual-approved change on their own. `reverse_amendment` did not exist before, so
every test here fails on the pre-fix code.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, UserProfile
from hris.amendment_models import HRISAmendment
from hris.amendment_service import (
    approve_amendment, reject_amendment, reverse_amendment, submit_amendment,
)
from payroll.models import Employee


class AmendmentReversalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='REV', name='Rev Co.')
        cls.emp = Employee.objects.create(
            employee_number='E200', full_name='Original Name',
            job_title='Clerk', company=cls.company,
        )
        # Thapelo is HR-authorised but NOT on the self-apply list, so his
        # amendments route through pending → approve (the path being tested).
        cls.thapelo = User.objects.create_user(
            'tmorapedi', email='tmorapedi@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.thapelo, is_administrator=True)
        cls.unami = User.objects.create_user(
            'ubutale', email='ubutale@alphadirect.co.bw', password='x')

    def _applied(self, **proposed):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk),
                             proposed=proposed or {'job_title': 'Senior Clerk'})
        approve_amendment(a, self.unami)
        a.refresh_from_db()
        return a

    # ---- the happy path ------------------------------------------------
    def test_a_reversal_parks_as_pending_and_does_not_touch_the_record(self):
        applied = self._applied(job_title='Senior Clerk')
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.job_title, 'Senior Clerk')

        rev = reverse_amendment(applied, self.thapelo)
        self.assertEqual(rev.status, HRISAmendment.Status.PENDING)
        self.assertEqual(rev.reversal_of_id, applied.pk)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.job_title, 'Senior Clerk')   # still not undone

    def test_the_reversal_swaps_old_and_new(self):
        applied = self._applied(job_title='Senior Clerk')
        rev = reverse_amendment(applied, self.thapelo)
        self.assertEqual(rev.changes['job_title']['old'], 'Senior Clerk')
        self.assertEqual(rev.changes['job_title']['new'], 'Clerk')

    def test_approving_the_reversal_restores_the_original_value(self):
        applied = self._applied(job_title='Senior Clerk')
        rev = reverse_amendment(applied, self.thapelo)
        approve_amendment(rev, self.unami)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.job_title, 'Clerk')

    def test_it_reverses_every_field_the_original_changed(self):
        applied = self._applied(job_title='Lead', full_name='Changed Name')
        rev = reverse_amendment(applied, self.thapelo)
        approve_amendment(rev, self.unami)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.job_title, 'Clerk')
        self.assertEqual(self.emp.full_name, 'Original Name')

    # ---- the controls it must NOT bypass -------------------------------
    def test_the_maker_of_the_reversal_cannot_approve_it(self):
        """Segregation of duties survives — this is the whole reason a reversal
        is an amendment rather than an undo button."""
        applied = self._applied(job_title='Senior Clerk')
        rev = reverse_amendment(applied, self.thapelo)
        with self.assertRaises(ValidationError):
            approve_amendment(rev, self.thapelo)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.job_title, 'Senior Clerk')   # nothing moved

    def test_a_pending_amendment_cannot_be_reversed(self):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk),
                             proposed={'job_title': 'Nope'})
        self.assertEqual(a.status, HRISAmendment.Status.PENDING)
        with self.assertRaises(ValidationError):
            reverse_amendment(a, self.thapelo)

    def test_a_rejected_amendment_cannot_be_reversed(self):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk),
                             proposed={'job_title': 'Nope'})
        reject_amendment(a, self.unami, notes='no')
        a.refresh_from_db()
        with self.assertRaises(ValidationError):
            reverse_amendment(a, self.thapelo)

    def test_reversing_twice_is_refused(self):
        applied = self._applied(job_title='Senior Clerk')
        reverse_amendment(applied, self.thapelo)
        with self.assertRaises(ValidationError):
            reverse_amendment(applied, self.thapelo)

    def test_a_rejected_reversal_frees_the_original_to_be_reversed_again(self):
        applied = self._applied(job_title='Senior Clerk')
        rev = reverse_amendment(applied, self.thapelo)
        reject_amendment(rev, self.unami, notes='changed my mind')
        again = reverse_amendment(applied, self.thapelo)
        self.assertEqual(again.status, HRISAmendment.Status.PENDING)

    def test_reversing_a_field_already_back_where_it_started_is_a_no_op_error(self):
        """`submit_amendment` reads `old` from the LIVE record, which is correct
        for a compensating entry — and means an already-restored field is
        honestly refused rather than silently creating an empty amendment."""
        applied = self._applied(job_title='Senior Clerk')
        back = submit_amendment(maker=self.thapelo, target_kind='employee',
                                target_id=str(self.emp.pk),
                                proposed={'job_title': 'Clerk'})
        approve_amendment(back, self.unami)
        with self.assertRaises(ValidationError):
            reverse_amendment(applied, self.thapelo)
