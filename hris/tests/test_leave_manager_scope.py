"""Leave-approver picker must be COMPANY-SCOPED — CFO directive 2026-07-25.

Reproduces the Motlatsi Molefe report: a Unicoin employee opening /hris/leave
was offered the whole Alpha Direct management line to approve her leave, while
her actual boss (a group manager carried on the Alpha Direct payroll) was
absent because nobody had him recorded as their line manager.

Locks three rules:
  1. You see managers of YOUR company's people — never another company's.
  2. You always see your own assigned line manager, even across companies
     (HR set that link deliberately — that's how the real boss appears).
  3. The submit endpoint enforces the SAME set, so a scoped dropdown cannot be
     bypassed by posting another company's manager id straight to the API.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee

# main added a >=15-word minimum on leave reasons (leave-accountability rules),
# so fixtures must satisfy it or every successful-apply assertion 400s.
REASON = (
    'Testing the company scoped approver routing for this leave request so that '
    'the reason clears the fifteen word minimum enforced by the accountability rules'
)

MANAGERS_URL = '/hris/api/leave-managers/'
APPLY_URL = '/hris/api/leave-requests/'


def _unlock(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    profile.save(update_fields=['hris_unlocked_until'])
    return profile


def _staff(username, name, company, *, email=None):
    """An employee with a working login, linked both ways."""
    user = User.objects.create_user(
        username=username, email=email or f'{username}@example.com', password='x')
    emp = Employee.objects.create(
        employee_number=username.upper(), full_name=name,
        company=company, email=user.email, user=user)
    _unlock(user)
    return user, emp


class LeaveManagerCompanyScopeTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code='TADIC', name='Alpha Direct Insurance (test)')
        cls.unicoin = Company.objects.create(code='TUNI', name='Unicoin (test)')

        # ── Alpha Direct side ────────────────────────────────────────────────
        # An ADIC manager with ADIC reports only — must NEVER show to Unicoin.
        cls.adic_mgr_user, cls.adic_mgr = _staff('adic_mgr', 'Kago Adic', cls.adic)
        cls.adic_staff_user, cls.adic_staff = _staff('adic_staff', 'Anna Adic', cls.adic)
        cls.adic_staff_profile = HRISProfile.objects.create(
            employee=cls.adic_staff, manager=cls.adic_mgr)
        HRISProfile.objects.create(employee=cls.adic_mgr)

        # ── The real-world case: a group manager on the ADIC payroll who runs
        #    a Unicoin employee (Bharath / Motlatsi). ─────────────────────────
        cls.group_mgr_user, cls.group_mgr = _staff('group_mgr', 'Bharath Group', cls.adic)
        HRISProfile.objects.create(employee=cls.group_mgr)

        cls.uni_user, cls.uni_emp = _staff('uni_staff', 'Motlatsi Unicoin', cls.unicoin)
        cls.uni_profile = HRISProfile.objects.create(
            employee=cls.uni_emp, manager=cls.group_mgr)

        # A second Unicoin employee with NO manager set — represents the rest
        # of the Unicoin roster.
        cls.uni2_user, cls.uni2_emp = _staff('uni_staff2', 'Bakang Unicoin', cls.unicoin)
        cls.uni2_profile = HRISProfile.objects.create(employee=cls.uni2_emp)

        cls.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual Leave', 'default_annual_days': 22})

    def _names(self, user):
        self.client.force_authenticate(user)
        resp = self.client.get(MANAGERS_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        return {m['name'] for m in resp.json()['managers']}, resp.json()

    # ── Rule 1 + 2: the picker ───────────────────────────────────────────────
    def test_unicoin_employee_never_sees_unrelated_alpha_direct_manager(self):
        names, _ = self._names(self.uni_user)
        self.assertNotIn('Kago Adic', names,
                         'Unicoin staff must not be offered an Alpha Direct-only manager')

    def test_unicoin_employee_sees_their_own_cross_company_boss(self):
        """Bharath is on the ADIC payroll but manages this Unicoin employee —
        HR set that link, so he must appear (and be pre-selected)."""
        names, payload = self._names(self.uni_user)
        self.assertIn('Bharath Group', names)
        self.assertEqual(payload['default_manager_id'], self.group_mgr_user.id)

    def test_alpha_direct_employee_does_not_see_unicoin_only_managers(self):
        names, _ = self._names(self.adic_staff_user)
        self.assertIn('Kago Adic', names)
        self.assertNotIn('Motlatsi Unicoin', names)

    def test_second_unicoin_employee_sees_the_group_manager_not_adic(self):
        """Once the group manager runs one Unicoin person he is the company's
        approver — visible to Unicoin colleagues, still hiding ADIC managers."""
        names, _ = self._names(self.uni2_user)
        self.assertIn('Bharath Group', names)
        self.assertNotIn('Kago Adic', names)

    def test_employee_is_never_offered_themselves(self):
        """A manager applying for their own leave must not self-approve."""
        names, _ = self._names(self.group_mgr_user)
        self.assertNotIn('Bharath Group', names)

    def test_empty_list_explains_itself(self):
        """No approver for your company → a plain reason, not a silent blank."""
        lonely_user, lonely_emp = _staff('lonely', 'Solo Person',
                                         Company.objects.create(code='TSOLO', name='Solo Co (test)'))
        HRISProfile.objects.create(employee=lonely_emp)
        names, payload = self._names(lonely_user)
        self.assertEqual(names, set())
        self.assertIn('Ask HR', payload.get('empty_reason', ''))

    # ── Rule 3: the server, not just the dropdown ────────────────────────────
    def _apply(self, user, approver_user):
        self.client.force_authenticate(user)
        return self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': '2026-08-10',
            'end_date': '2026-08-10',
            'approver_id': str(approver_user.id),
            'reason': REASON,
        })

    def test_cannot_post_another_companys_manager(self):
        """The hole this closes: scoping the dropdown alone left the API open."""
        resp = self._apply(self.uni_user, self.adic_mgr_user)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('list', resp.json()['detail'].lower())

    def test_can_post_own_cross_company_boss(self):
        resp = self._apply(self.uni_user, self.group_mgr_user)
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_userprofile_has_no_employee_link(self):
        """Guards the assumption leave_manager_employee_ids relies on.

        Fable 5's review flagged that _profile_for resolves user→Employee two
        ways and that scoping on only the first would let a "legacy-linked" user
        reach the unrestricted branch. Checked: core.models.UserProfile has NO
        employee field, so that second branch can never fire and the hole cannot
        occur. If this test ever fails, UserProfile gained an employee FK and
        leave_manager_employee_ids must resolve it too.
        """
        from core.models import UserProfile
        self.assertNotIn('employee', [f.name for f in UserProfile._meta.get_fields()])

    def test_terminated_manager_is_never_offered(self):
        """DeepSeek review 2026-07-25: a leaver whose login is still switched on
        must not be offered as an approver — leave would route to someone gone."""
        leaver_user, leaver = _staff('leaver_mgr', 'Gone Manager', self.unicoin)
        HRISProfile.objects.create(employee=leaver, manager=self.group_mgr)
        staff_user, staff = _staff('uni_staff3', 'Reports To Leaver', self.unicoin)
        HRISProfile.objects.create(employee=staff, manager=leaver)

        names, _ = self._names(staff_user)
        self.assertIn('Gone Manager', names, 'sanity: visible while still employed')

        leaver.status = 'terminated'
        leaver.save(update_fields=['status'])
        names, payload = self._names(staff_user)
        self.assertNotIn('Gone Manager', names)
        # Never pre-select an approver the server would reject (Fable 5 review).
        self.assertNotEqual(payload.get('default_manager_id'), leaver_user.id)
        # and cannot be forced through the API either
        resp = self._apply(staff_user, leaver_user)
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_user_without_employee_record_sees_nothing(self):
        """DeepSeek review 2026-07-25: the earlier 'no scope -> do not restrict'
        sentinel handed the WHOLE group's managers to any account with no
        employee record. There is no unrestricted escape hatch any more."""
        loose = User.objects.create_user(
            username='loose_admin', email='loose_admin@example.com', password='x')
        _unlock(loose)
        names, payload = self._names(loose)
        self.assertEqual(names, set(),
                         'an account with no employee record must see no managers')
        self.assertIn('Ask HR', payload.get('empty_reason', ''))

        # ...and it cannot be bypassed by posting an approver id directly.
        resp = self._apply(loose, self.adic_mgr_user)
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_cross_company_approver_still_receives_the_email(self):
        """DeepSeek flagged that a cross-company manager might not see the
        entity-scoped approval queue. The email path is the guarantee the
        request is never orphaned — lock it down."""
        from hris.leave_email import build_leave_email
        resp = self._apply(self.uni_user, self.group_mgr_user)
        self.assertEqual(resp.status_code, 201, resp.content)
        lr = LeaveRequest.objects.get(pk=resp.json()['id'])
        self.assertEqual(lr.requested_approver_id, self.group_mgr_user.id)
        mail = build_leave_email(lr)
        self.assertIsNotNone(mail, 'cross-company approver must still be emailed')
        recipients = mail['to'] if isinstance(mail['to'], (list, tuple)) else [mail['to']]
        self.assertIn(self.group_mgr_user.email, recipients)

    def test_cannot_apply_with_no_approver_when_no_line_manager(self):
        """Fable 5 review: with no approver chosen AND no line manager,
        build_leave_email returns None — the request would sit PENDING and
        notify nobody. Every Unicoin employee is in that state today."""
        self.client.force_authenticate(self.uni2_user)
        resp = self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': '2026-08-11',
            'end_date': '2026-08-11',
            'reason': REASON,
        })
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('ask HR', resp.json()['detail'])

    def test_legacy_behaviour_kept_when_line_manager_exists(self):
        """Omitting approver_id is still fine when HR set a line manager — the
        pre-picker fallback path must not break."""
        self.client.force_authenticate(self.adic_staff_user)
        resp = self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': '2026-08-12',
            'end_date': '2026-08-12',
            'reason': REASON,
        })
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_onboarding_default_leave_approver_is_preselected_and_used(self):
        """A stored onboarding assignment routes leave even when no line manager
        FK is present and the employee does not post an explicit approver id."""
        self.uni2_profile.default_leave_approver = self.group_mgr_user
        self.uni2_profile.save(update_fields=['default_leave_approver'])

        names, payload = self._names(self.uni2_user)
        self.assertIn('Bharath Group', names)
        self.assertEqual(payload['default_manager_id'], self.group_mgr_user.id)

        self.client.force_authenticate(self.uni2_user)
        resp = self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': '2026-08-13',
            'end_date': '2026-08-13',
            'reason': REASON,
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        leave = LeaveRequest.objects.get(pk=resp.json()['id'])
        self.assertEqual(leave.requested_approver_id, self.group_mgr_user.id)
