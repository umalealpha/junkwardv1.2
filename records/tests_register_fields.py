"""Department + the day counter on the Records Register.

Tlotlo Maswabi's request; CFO cleared it 2026-08-07 ("do the building, keep it
out of fixed asset register as a separate thing").

The day counter answers "how long has this physical file been out of the
repository" — NOT "how long has the current person had it". A transfer hands the
file on without it coming back, so the clock must keep running.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import Company
from records.models import RecordCategory, RecordItem, RecordMovement
from records.services import move_record


class RegisterFieldsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='RGF', name='ADIC (register test)')
        cls.cat = RecordCategory.objects.create(name='Policy files')
        cls.user = User.objects.create_user('rgfuser', 'rgf@alphadirect.co.bw', 'x')

    def _rec(self, ref='RGF-1', department=''):
        return RecordItem.objects.create(
            reference=ref, title='A file', category=self.cat,
            company=self.co, department=department)

    # ── department ──────────────────────────────────────────────────────────
    def test_a_record_carries_its_department(self):
        rec = self._rec(department='Claims')
        rec.refresh_from_db()
        self.assertEqual(rec.department, 'Claims')

    def test_department_is_optional(self):
        self.assertEqual(self._rec(ref='RGF-2').department, '')

    # ── the day counter ─────────────────────────────────────────────────────
    def test_a_file_in_the_store_has_no_day_count(self):
        rec = self._rec(ref='RGF-3')
        self.assertFalse(rec.is_out)
        self.assertIsNone(rec.days_out)

    def test_issuing_starts_the_clock_at_zero(self):
        rec = self._rec(ref='RGF-4')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate(), recorded_by=self.user,
                    to_custodian='Tlotlo Maswabi')
        rec.refresh_from_db()
        self.assertTrue(rec.is_out)
        self.assertEqual(rec.days_out, 0, 'the day it leaves reads as today')

    def test_the_count_grows_with_the_days(self):
        rec = self._rec(ref='RGF-5')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate() - dt.timedelta(days=9),
                    recorded_by=self.user, to_custodian='Tlotlo Maswabi')
        rec.refresh_from_db()
        self.assertEqual(rec.days_out, 9)

    def test_returning_it_stops_and_clears_the_clock(self):
        rec = self._rec(ref='RGF-6')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate() - dt.timedelta(days=5),
                    recorded_by=self.user, to_custodian='Tlotlo Maswabi')
        move_record(rec, kind=RecordMovement.Kind.RETURN,
                    moved_at=timezone.localdate(), recorded_by=self.user)
        rec.refresh_from_db()
        self.assertIsNone(rec.out_since)
        self.assertIsNone(rec.days_out)

    def test_a_transfer_does_NOT_restart_the_clock(self):
        """The number Admin needs is time out of the REPOSITORY. Passing the file
        to a second person does not mean it came back."""
        rec = self._rec(ref='RGF-7')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate() - dt.timedelta(days=20),
                    recorded_by=self.user, to_custodian='First Person')
        move_record(rec, kind=RecordMovement.Kind.TRANSFER,
                    moved_at=timezone.localdate() - dt.timedelta(days=2),
                    recorded_by=self.user, to_custodian='Second Person')
        rec.refresh_from_db()
        self.assertEqual(rec.days_out, 20, 'a transfer must not reset the count to 2')

    def test_archiving_clears_it_too(self):
        rec = self._rec(ref='RGF-8')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate() - dt.timedelta(days=3),
                    recorded_by=self.user, to_custodian='Someone')
        move_record(rec, kind=RecordMovement.Kind.ARCHIVE,
                    moved_at=timezone.localdate(), recorded_by=self.user)
        rec.refresh_from_db()
        self.assertIsNone(rec.days_out)

    def test_reissuing_after_a_return_starts_a_fresh_count(self):
        rec = self._rec(ref='RGF-9')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate() - dt.timedelta(days=30),
                    recorded_by=self.user, to_custodian='A')
        move_record(rec, kind=RecordMovement.Kind.RETURN,
                    moved_at=timezone.localdate() - dt.timedelta(days=10),
                    recorded_by=self.user)
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate() - dt.timedelta(days=1),
                    recorded_by=self.user, to_custodian='B')
        rec.refresh_from_db()
        self.assertEqual(rec.days_out, 1, 'a new spell out counts from the new issue')

    # ── the receiving party, which already existed ───────────────────────────
    def test_the_receiving_party_is_recorded_and_exposed(self):
        """Tlotlo also asked to capture who receives the file. That already
        worked — this pins it so it cannot regress."""
        from records.serializers import RecordItemSerializer
        rec = self._rec(ref='RGF-10')
        move_record(rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=timezone.localdate(), recorded_by=self.user,
                    to_custodian='Tlotlo Maswabi')
        rec.refresh_from_db()
        data = RecordItemSerializer(rec).data
        self.assertEqual(data['held_by'], 'Tlotlo Maswabi')
        self.assertEqual(data['days_out'], 0)
        self.assertIn('department', data)
