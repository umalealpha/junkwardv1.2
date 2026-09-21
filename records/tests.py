"""records/tests.py — the register's two load-bearing promises.

1. A movement and the item's position never disagree.
2. Legal hold beats retention, always.
"""
import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Company
from payroll.models import Employee
from records.models import RecordCategory, RecordItem, RecordMovement
from records.services import RecordMovementError, move_record

User = get_user_model()


class RecordMovementTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('clerk', 'clerk@alphadirect.co.bw', 'x')
        cls.company = Company.objects.create(code='TEST', name='Test Co.')
        cls.cat = RecordCategory.objects.create(name='HR files', retention_months=72)
        cls.emp = Employee.objects.create(
            employee_number='E100', full_name='Ambrose Keta',
            job_title='Clerk', department='Admin', company=cls.company)
        cls.rec = RecordItem.objects.create(
            reference='HR-0001', title='Staff file', category=cls.cat,
            company=cls.company, current_location='Storeroom A')

    def test_issue_moves_the_item_and_records_who_had_it_before(self):
        mv = move_record(self.rec, kind=RecordMovement.Kind.ISSUE,
                         moved_at=dt.date(2026, 8, 6), recorded_by=self.user,
                         to_employee=self.emp, to_location='Legal office',
                         reason='Disciplinary matter',
                         due_back_on=dt.date(2026, 8, 13))
        self.rec.refresh_from_db()
        # the item now reflects the movement — the register's whole job
        self.assertEqual(self.rec.status, RecordItem.Status.ISSUED)
        self.assertEqual(self.rec.current_holder, self.emp)
        self.assertEqual(self.rec.current_location, 'Legal office')
        # and the movement kept where it came FROM
        self.assertEqual(mv.from_location, 'Storeroom A')
        self.assertIsNone(mv.from_employee)

    def test_return_puts_it_back_in_store(self):
        move_record(self.rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=dt.date(2026, 8, 6), recorded_by=self.user,
                    to_employee=self.emp, to_location='Legal office')
        move_record(self.rec, kind=RecordMovement.Kind.RETURN,
                    moved_at=dt.date(2026, 8, 7), recorded_by=self.user,
                    to_location='Storeroom A')
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, RecordItem.Status.IN_STORE)
        self.assertIsNone(self.rec.current_holder)
        self.assertEqual(self.rec.movements.count(), 2)

    def test_legal_hold_blocks_destruction_even_when_retention_has_expired(self):
        self.rec.retention_until = dt.date(2020, 1, 1)   # long past
        self.rec.legal_hold = True
        self.rec.legal_hold_note = 'Held for the Motsepe matter'
        self.rec.save()
        with self.assertRaises(RecordMovementError) as ctx:
            move_record(self.rec, kind=RecordMovement.Kind.DESTROY,
                        moved_at=dt.date(2026, 8, 6), recorded_by=self.user)
        self.assertIn('legal hold', str(ctx.exception).lower())
        self.rec.refresh_from_db()
        self.assertNotEqual(self.rec.status, RecordItem.Status.DESTROYED)

    def test_destruction_refused_before_the_retention_date(self):
        self.rec.retention_until = dt.date(2030, 1, 1)
        self.rec.save()
        with self.assertRaises(RecordMovementError):
            move_record(self.rec, kind=RecordMovement.Kind.DESTROY,
                        moved_at=dt.date(2026, 8, 6), recorded_by=self.user)

    def test_destruction_allowed_once_retention_has_passed_and_no_hold(self):
        self.rec.retention_until = dt.date(2020, 1, 1)
        self.rec.save()
        move_record(self.rec, kind=RecordMovement.Kind.DESTROY,
                    moved_at=dt.date(2026, 8, 6), recorded_by=self.user)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, RecordItem.Status.DESTROYED)

    def test_a_destroyed_record_cannot_move_again(self):
        self.rec.retention_until = dt.date(2020, 1, 1)
        self.rec.save()
        move_record(self.rec, kind=RecordMovement.Kind.DESTROY,
                    moved_at=dt.date(2026, 8, 6), recorded_by=self.user)
        self.rec.refresh_from_db()
        with self.assertRaises(RecordMovementError):
            move_record(self.rec, kind=RecordMovement.Kind.ISSUE,
                        moved_at=dt.date(2026, 8, 7), recorded_by=self.user,
                        to_employee=self.emp)

    def test_a_reissued_record_does_not_stay_overdue_for_ever(self):
        """Fable review finding: the overdue list joined ALL history."""
        move_record(self.rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=dt.date(2026, 7, 1), recorded_by=self.user,
                    to_employee=self.emp, due_back_on=dt.date(2026, 7, 5))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.due_back_on, dt.date(2026, 7, 5))
        # returned — the old due date must not haunt it
        move_record(self.rec, kind=RecordMovement.Kind.RETURN,
                    moved_at=dt.date(2026, 7, 20), recorded_by=self.user,
                    to_location='Storeroom A')
        self.rec.refresh_from_db()
        self.assertIsNone(self.rec.due_back_on)
        # issued again with a future date — still not overdue
        move_record(self.rec, kind=RecordMovement.Kind.ISSUE,
                    moved_at=dt.date(2026, 8, 1), recorded_by=self.user,
                    to_employee=self.emp, due_back_on=dt.date(2099, 1, 1))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.due_back_on, dt.date(2099, 1, 1))

    def test_issuing_to_nobody_is_refused(self):
        with self.assertRaises(RecordMovementError) as ctx:
            move_record(self.rec, kind=RecordMovement.Kind.ISSUE,
                        moved_at=dt.date(2026, 8, 6), recorded_by=self.user)
        self.assertIn('nobody', str(ctx.exception).lower())


class RecordApiGateTests(TestCase):
    """The access promises, at the API layer — the gap Fable named."""

    @classmethod
    def setUpTestData(cls):
        from core.models import UserProfile
        cls.company = Company.objects.create(code='TST2', name='Test Two')
        cls.cat = RecordCategory.objects.create(name='HR')
        cls.open_rec = RecordItem.objects.create(
            reference='OPEN-1', title='Ordinary file', category=cls.cat)
        cls.secret = RecordItem.objects.create(
            reference='HR-9', title='Staff file', category=cls.cat,
            confidentiality=RecordItem.Confidentiality.RESTRICTED)
        cls.accountant = User.objects.create_user('acc', 'acc@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.accountant, defaults={'title': 'accountant'})
        cls.outsider = User.objects.create_user('out', 'out@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.outsider, defaults={'title': 'junior_claims_associate'})

    def _list(self, user):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(user=user)
        return c.get('/api/v1/records/')

    def test_a_role_outside_the_register_is_refused(self):
        self.assertEqual(self._list(self.outsider).status_code, 403)

    def test_an_accountant_sees_the_register_but_not_restricted_records(self):
        r = self._list(self.accountant)
        self.assertEqual(r.status_code, 200)
        refs = {row['reference'] for row in r.json()['results']}
        self.assertIn('OPEN-1', refs)
        self.assertNotIn('HR-9', refs)   # personal data stays hidden

    def test_a_restricted_record_is_404_by_id_not_just_absent_from_the_list(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(user=self.accountant)
        self.assertEqual(c.get(f'/api/v1/records/{self.secret.id}/').status_code, 404)

    def test_delete_is_not_exposed(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(user=self.accountant)
        self.assertIn(c.delete(f'/api/v1/records/{self.open_rec.id}/').status_code,
                      (403, 405))

    def test_patch_cannot_move_a_record_without_a_movement(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(user=self.accountant)
        c.patch(f'/api/v1/records/{self.open_rec.id}/',
                {'status': 'issued', 'current_location': 'Nowhere'}, format='json')
        self.open_rec.refresh_from_db()
        self.assertEqual(self.open_rec.status, RecordItem.Status.IN_STORE)
        self.assertEqual(self.open_rec.movements.count(), 0)
