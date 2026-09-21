"""Tests for the HRIS amendment workflow.

CFO directive 2026-06-25: Unami (ubutale) and Dorothy (dikgopoleng) now apply
their HRIS amendments DIRECTLY — no second approver (see
amendment_service._can_self_apply / SELFAPPLY_DEFAULT_LOCAL_PARTS). The
maker-checker (submit -> pending -> approve) path still applies to every OTHER
HRIS editor, so those tests use Thapelo (tmorapedi), who is NOT on the
self-apply list.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, UserProfile
from payroll.models import Employee
from hris.amendment_models import HRISAmendment
from hris.models import HRISProfile
from hris.amendment_service import (
    approve_amendment, approver_email_for, reject_amendment, submit_amendment,
)


class HRISAmendmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TEST', name='Test Co.')
        cls.emp = Employee.objects.create(
            employee_number='E100', full_name='Old Name',
            job_title='Clerk', company=cls.company,
        )
        cls.unami   = User.objects.create_user('ubutale',     email='ubutale@alphadirect.co.bw',     password='x')
        cls.dorothy = User.objects.create_user('dikgopoleng', email='dikgopoleng@alphadirect.co.bw', password='x')
        cls.thapelo = User.objects.create_user('tmorapedi',   email='tmorapedi@alphadirect.co.bw',   password='x')  # NOT self-apply
        cls.cfo     = User.objects.create_user('pg',          email='pganesharajah@alphadirect.co.bw', password='x')
        cls.stranger = User.objects.create_user('rando',      email='rando@elsewhere.com',           password='x')
        # BUG 16d631ce (2026-06-26): amending ANOTHER employee is now limited to
        # HR-admin / CFO / the target's manager (_enforce_amendment_scope). The
        # maker-checker tests have Thapelo propose changes to `emp` (not himself),
        # so he must be HR-authorised — but he stays OFF the self-apply list, so
        # submit still routes through the pending → approve path being tested.
        UserProfile.objects.create(user=cls.thapelo, is_administrator=True)

    # ---- approver routing ----
    def test_routing_team_member_goes_to_unami(self):
        self.assertEqual(approver_email_for(self.dorothy), 'ubutale@alphadirect.co.bw')

    def test_routing_unami_escalates_to_cfo(self):
        self.assertEqual(approver_email_for(self.unami), 'pganesharajah@alphadirect.co.bw')

    # ---- self-apply (CFO directive 2026-06-25): Unami & Dorothy edit directly ----
    def test_unami_amendment_applies_immediately(self):
        a = submit_amendment(maker=self.unami, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'job_title': 'Lead'})
        self.assertEqual(a.status, HRISAmendment.Status.APPROVED)   # no pending step
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.job_title, 'Lead')                # live record changed

    def test_dorothy_amendment_applies_immediately(self):
        a = submit_amendment(maker=self.dorothy, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'full_name': 'Direct Edit'})
        self.assertEqual(a.status, HRISAmendment.Status.APPROVED)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.full_name, 'Direct Edit')

    # ---- submit (maker-checker path: non-self-apply editor = Thapelo) ----
    def test_submit_creates_pending_without_touching_live(self):
        a = submit_amendment(
            maker=self.thapelo, target_kind='employee', target_id=str(self.emp.pk),
            proposed={'full_name': 'New Name', 'job_title': 'Senior Clerk'},
            reason='Promotion',
        )
        self.assertEqual(a.status, HRISAmendment.Status.PENDING)
        self.assertEqual(a.approver_email, 'ubutale@alphadirect.co.bw')
        self.assertIn('full_name', a.changes)
        self.assertEqual(a.changes['full_name']['old'], 'Old Name')
        self.assertEqual(a.changes['full_name']['new'], 'New Name')
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.full_name, 'Old Name')   # unchanged until approved

    def test_submit_rejects_non_amendable_field(self):
        with self.assertRaises(ValidationError):
            submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'id': 'hack'})

    def test_submit_noop_change_raises(self):
        with self.assertRaises(ValidationError):
            submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'full_name': 'Old Name'})

    # ---- approve / reject (maker-checker path) ----
    def test_approve_applies_change(self):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'full_name': 'New Name'})
        approve_amendment(a, self.unami)
        a.refresh_from_db()
        self.emp.refresh_from_db()
        self.assertEqual(a.status, HRISAmendment.Status.APPROVED)
        self.assertEqual(self.emp.full_name, 'New Name')

    def test_maker_cannot_approve_own(self):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'full_name': 'New Name'})
        with self.assertRaises(ValidationError):
            approve_amendment(a, self.thapelo)        # SoD
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.full_name, 'Old Name')

    def test_unauthorised_user_cannot_approve(self):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'full_name': 'New Name'})
        with self.assertRaises(ValidationError):
            approve_amendment(a, self.stranger)

    def test_reject_leaves_record_unchanged(self):
        a = submit_amendment(maker=self.thapelo, target_kind='employee',
                             target_id=str(self.emp.pk), proposed={'full_name': 'New Name'})
        reject_amendment(a, self.unami, notes='Not authorised')
        a.refresh_from_db()
        self.emp.refresh_from_db()
        self.assertEqual(a.status, HRISAmendment.Status.REJECTED)
        self.assertEqual(self.emp.full_name, 'Old Name')


class ReportingLineAmendmentTest(TestCase):
    """Reporting lines (HRISProfile.manager) via the amendment flow.

    CFO 2026-07-26: the CFO asked for a way to change titles and reporting lines
    in Omni instead of by hand. The capability already existed, but `manager_id`
    carried three prior bug references (f05470e2 / 6b12dde5 / HRIS-004 — a typed
    manager name silently never saved) and had NO test. These cover it: the field
    is amendable, it applies to the live record, and it survives maker-checker.
    """

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='RL', name='Reporting Line Co.')
        cls.staff = Employee.objects.create(
            employee_number='E200', full_name='Reporting Staff',
            job_title='Associate', company=cls.company)
        cls.old_mgr = Employee.objects.create(
            employee_number='E201', full_name='Old Manager', company=cls.company)
        cls.new_mgr = Employee.objects.create(
            employee_number='E202', full_name='New Manager', company=cls.company)
        cls.profile = HRISProfile.objects.create(employee=cls.staff, manager=cls.old_mgr)

        cls.unami = User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw', password='x')
        cls.thapelo = User.objects.create_user('tmorapedi', email='tmorapedi@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.thapelo, is_administrator=True)

    def test_manager_id_is_amendable_and_applies(self):
        """Self-apply path: HR changes the reporting line, live record follows."""
        a = submit_amendment(
            maker=self.unami, target_kind='profile', target_id=str(self.profile.pk),
            proposed={'manager_id': str(self.new_mgr.pk)}, reason='Restructure')
        self.assertEqual(a.status, HRISAmendment.Status.APPROVED)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.manager_id, self.new_mgr.pk)

    def test_manager_change_records_old_and_new(self):
        """The audit diff must name both managers — an org-chart change has to be
        reconstructable later, not just 'manager changed'."""
        a = submit_amendment(
            maker=self.unami, target_kind='profile', target_id=str(self.profile.pk),
            proposed={'manager_id': str(self.new_mgr.pk)}, reason='Restructure')
        self.assertIn('manager_id', a.changes)
        self.assertEqual(a.changes['manager_id']['old'], str(self.old_mgr.pk))
        self.assertEqual(a.changes['manager_id']['new'], str(self.new_mgr.pk))

    def test_manager_change_holds_until_approved(self):
        """Maker-checker path: the reporting line must NOT move on submit."""
        a = submit_amendment(
            maker=self.thapelo, target_kind='profile', target_id=str(self.profile.pk),
            proposed={'manager_id': str(self.new_mgr.pk)}, reason='Restructure')
        self.assertEqual(a.status, HRISAmendment.Status.PENDING)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.manager_id, self.old_mgr.pk)   # still the old one

        approve_amendment(a, self.unami)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.manager_id, self.new_mgr.pk)

    def test_job_title_change_does_not_touch_access_title(self):
        """The display job_title and the access-role `title` are different fields.
        Editing the visible one must never move the one that grants approvals."""
        before = getattr(self.staff, 'title', None)
        submit_amendment(
            maker=self.unami, target_kind='employee', target_id=str(self.staff.pk),
            proposed={'job_title': 'Senior Accountant - Finance & Planning'},
            reason='Promotion')
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.job_title, 'Senior Accountant - Finance & Planning')
        self.assertEqual(getattr(self.staff, 'title', None), before)
