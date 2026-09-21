"""Creating a reinsurer — the missing front door.

The whole onboarding chain (Draft → Underwriting → Compliance → Principal →
CEO → Approved) was built and shipped with no way to START it: Underwriting
could not raise a counterparty without a developer writing a row by hand. CFO,
17-Sep-2026: "create the new reinsuer buttun please".

The two things that must hold:
  1. A brand-new counterparty is a DRAFT and is NOT placeable. "Just created"
     must never read as "fine to use" — the point of the whole chain.
  2. The person who may create is the person who may submit. Creating is step
     zero of the same workflow, so it reuses `uw.counterparty.submit` rather
     than inventing a second answer to "who may start this".

RED-PROOF:
  * Change CREATE_PERM to 're.view' → `test_a_reader_cannot_create` fails
    (a read-only auditor gets a 201).
  * Drop the duplicate check → `test_a_duplicate_short_code_is_refused` fails.
  * Narrow `actionable_states` back to `pending_states` in
    `controls_api.reinsurance_control_centre` → `test_a_new_draft_reaches_the
    _queue_that_carries_the_submit_button` fails.

An earlier version of this file also claimed "drop the `approval_status=DRAFT`
default from the view and the not-placeable test fails". That was never run and
is FALSE — `models.py` already defaults the column to DRAFT, so removing it from
the view changes nothing and the test stays green. A red-proof you assert
instead of running is worth less than no red-proof, because it is believed.
The date-parsing block it also referred to has been removed outright: the form
sends no dates and `full_clean` already refuses a bad one.
"""
from __future__ import annotations

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Permission, Role, UserRoleAssignment
from reinsurance.controls_api import CREATE_PERM
from reinsurance.models import Reinsurer

S = Reinsurer.ApprovalStatus
URL = '/api/v1/reinsurance/counterparties/new/'


def _user_with(perms, username, *, level=3):
    """A real user holding a real role — not a superuser shortcut.

    A superuser passes every permission check, so a test that only ever
    authenticates as one proves nothing about the gate.
    """
    user = User.objects.create_user(username, f'{username}@alphadirect.co.bw', 'x')
    role = Role.objects.create(code=f'ROLE_{username.upper()}',
                               name=f'Role {username}', level=level)
    for code in perms:
        perm, _ = Permission.objects.get_or_create(
            code=code, defaults={'category': 'test', 'description': code})
        role.permissions.add(perm)
    UserRoleAssignment.objects.create(user=user, role=role)
    return user


class ReinsurerCreateTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.creator = _user_with(['uw.counterparty.submit', 're.view'], 'uwmgr')
        cls.reader = _user_with(['re.view'], 'auditor')

    def setUp(self):
        self.api = APIClient()

    def _post(self, user, **payload):
        self.api.force_authenticate(user)
        body = {'name': 'Swiss Re', 'short_code': 'SWISSRE'}
        body.update(payload)
        return self.api.post(URL, body, format='json')

    # -- the gate ---------------------------------------------------------

    def test_a_reader_cannot_create(self):
        res = self._post(self.reader)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Reinsurer.objects.count(), 0)

    def test_a_stranger_cannot_create(self):
        outsider = User.objects.create_user('nobody', 'nobody@x.bw', 'x')
        res = self._post(outsider)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Reinsurer.objects.count(), 0)

    def test_underwriting_can_create(self):
        res = self._post(self.creator)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Reinsurer.objects.count(), 1)

    # -- what a new row IS -------------------------------------------------

    def test_a_new_counterparty_is_a_draft_and_cannot_be_placed(self):
        res = self._post(self.creator)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['approval_status'], S.DRAFT)
        self.assertFalse(res.data['may_be_placed'],
                         'a brand-new counterparty was immediately placeable')
        self.assertTrue(res.data['placement_block_reason'],
                        'it is blocked but does not say why')
        r = Reinsurer.objects.get()
        self.assertFalse(r.may_be_placed())

    def test_the_identity_fields_are_stored(self):
        res = self._post(self.creator, legal_name='Swiss Reinsurance Company Ltd',
                         domicile='CH', regulator='FINMA', broker='Aon',
                         carrier_group='Swiss Re Group', credit_rating='AA-')
        self.assertEqual(res.status_code, 201, res.data)
        r = Reinsurer.objects.get()
        self.assertEqual(r.legal_name, 'Swiss Reinsurance Company Ltd')
        self.assertEqual(r.domicile, 'CH')
        self.assertEqual(r.regulator, 'FINMA')
        self.assertEqual(r.carrier_group, 'Swiss Re Group')

    # -- what it refuses ---------------------------------------------------

    def test_a_nameless_counterparty_is_refused(self):
        res = self._post(self.creator, name='')
        self.assertEqual(res.status_code, 400)
        self.assertIn('name', res.data['detail'].lower())

    def test_a_counterparty_with_no_short_code_is_refused(self):
        res = self._post(self.creator, short_code='   ')
        self.assertEqual(res.status_code, 400)
        self.assertIn('short code', res.data['detail'].lower())

    def test_a_duplicate_short_code_is_refused_and_names_the_record(self):
        Reinsurer.objects.create(name='Swiss Re AG', short_code='SWISSRE')
        res = self._post(self.creator, name='Something Else')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Swiss Re AG', res.data['detail'])
        self.assertEqual(Reinsurer.objects.count(), 1)

    def test_a_duplicate_name_is_refused_case_insensitively(self):
        Reinsurer.objects.create(name='Swiss Re', short_code='OTHER')
        res = self._post(self.creator, name='swiss re', short_code='NEWCODE')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Reinsurer.objects.count(), 1)

    def test_a_too_long_value_names_the_field(self):
        res = self._post(self.creator, short_code='X' * 25)
        self.assertEqual(res.status_code, 400)
        self.assertIn('short code', res.data['detail'].lower(),
                      'the person is told the value is wrong but not which box')

    def test_an_unknown_purpose_is_refused(self):
        res = self._post(self.creator, onboarding_purposes=['facultative', 'crypto'])
        self.assertEqual(res.status_code, 400)
        self.assertIn('crypto', res.data['detail'])
        self.assertEqual(Reinsurer.objects.count(), 0)

    def test_the_purposes_it_is_onboarded_for_are_stored(self):
        res = self._post(self.creator, onboarding_purposes=['facultative', 'treaty'])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Reinsurer.objects.get().onboarding_purposes,
                         ['facultative', 'treaty'])


class DraftReachesTheQueueTests(TestCase):
    """The defect Fable caught: a front door that opens onto a wall.

    Creating the counterparty is only half the job. The ONLY screen that offers
    "submit for underwriting review" is the control-centre queue, and that queue
    filtered on the four PENDING_* statuses — so a brand-new Draft was created
    and then had no button anywhere to move it on. The whole chain would still
    have had no way in, one step further along.
    """

    @classmethod
    def setUpTestData(cls):
        cls.creator = _user_with(
            ['uw.counterparty.submit', 're.view'], 'queuemgr')

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.creator)

    def _queue(self):
        res = self.api.get('/api/v1/reinsurance/controls/')
        self.assertEqual(res.status_code, 200, res.data)
        return {row['id']: row for row in res.data['queue']}

    def test_a_new_draft_reaches_the_queue_that_carries_the_submit_button(self):
        created = self.api.post(URL, {'name': 'Queue Re', 'short_code': 'QUEUERE'},
                                format='json')
        self.assertEqual(created.status_code, 201, created.data)
        new_id = created.data['id']

        row = self._queue().get(new_id)
        self.assertIsNotNone(row, 'the new draft is not in the queue, so nobody '
                                  'can submit it')
        allowed = [a['target'] for a in row['available_actions'] if a['allowed']]
        self.assertIn(S.PENDING_UW_MANAGER, allowed,
                      'the draft is listed but carries no way forward')

    def test_a_returned_counterparty_is_not_stranded_either(self):
        """Same gap, already live before this change: RETURNED and REJECTED
        rows leave only via PENDING_UW_MANAGER and were not in the queue."""
        for status in (S.RETURNED, S.REJECTED, S.EXPIRED):
            with self.subTest(status=status):
                r = Reinsurer.objects.create(
                    name=f'Sent Back {status}', short_code=f'SB{status[:5].upper()}',
                    approval_status=status)
                self.assertIn(str(r.pk), self._queue(),
                              f'a {status} counterparty has no screen to act on it')

    def test_an_unsubmitted_draft_sorts_above_rows_already_in_flight(self):
        """Postgres puts NULL last on a plain ASC, which buried every new draft
        under the rows already waiting."""
        Reinsurer.objects.create(name='In Flight', short_code='INFLIGHT',
                                 approval_status=S.PENDING_COMPLIANCE,
                                 submitted_at=timezone.now())
        created = self.api.post(URL, {'name': 'Fresh Re', 'short_code': 'FRESHRE'},
                                format='json')
        self.assertEqual(created.status_code, 201)
        order = [row['id'] for row in
                 self.api.get('/api/v1/reinsurance/controls/').data['queue']]
        self.assertEqual(order[0], created.data['id'],
                         'the new draft was sorted below the rows in flight')


class RealRolesCanUseTheButtonTests(TestCase):
    """The synthetic-role tests above prove the GATE works. They do not prove
    any role a real person actually holds is on the right side of it — which is
    the failure mode that keeps reaching the CFO as "the button does nothing".
    """

    @classmethod
    def setUpTestData(cls):
        call_command('seed_roles', '--skip-bootstrap', verbosity=0)

    def _codes(self, role_code):
        role = Role.objects.filter(code=role_code).first()
        self.assertIsNotNone(role, f'{role_code} is not in the catalogue')
        return set(role.permissions.values_list('code', flat=True))

    def test_underwriting_management_can_raise_a_counterparty(self):
        for code in ('HEAD_UNDERWRITING', 'UNDERWRITING_MANAGER'):
            self.assertIn(CREATE_PERM, self._codes(code),
                          f'{code} cannot create a reinsurer')

    def test_it_is_not_open_to_everyone_who_can_merely_look(self):
        for code in ('UNDERWRITER', 'SENIOR_UNDERWRITER', 'RI_OFFICER',
                     'IT_MANAGER', 'INTERNAL_AUDITOR'):
            self.assertNotIn(CREATE_PERM, self._codes(code),
                             f'{code} can raise a counterparty')


class ReinsurerIntakeRoleTests(TestCase):
    """The role the CFO's 13 people hold.

    The button was only half the answer: on prod only 15 of 201 active staff
    held ANY role, and nobody at all held the two the permission sits under.
    This role exists so those 13 can raise a counterparty WITHOUT also being
    handed policy binding and pricing rights, which is what assigning
    UNDERWRITING_MANAGER would have done as a side effect.
    """

    @classmethod
    def setUpTestData(cls):
        call_command('seed_roles', '--skip-bootstrap', verbosity=0)
        cls.person = User.objects.create_user(
            'intaker', 'intaker@alphadirect.co.bw', 'x')
        call_command('seed_reinsurer_intake_role', '--commit',
                     '--emails', cls.person.email, verbosity=0)

    def setUp(self):
        self.api = APIClient()

    def test_a_named_person_can_raise_a_counterparty(self):
        self.api.force_authenticate(self.person)
        res = self.api.post(URL, {'name': 'Intake Re', 'short_code': 'INTAKERE'},
                            format='json')
        self.assertEqual(res.status_code, 201, res.data)

    def test_the_role_holds_only_the_two_permissions(self):
        role = Role.objects.get(code='REINSURER_INTAKE')
        self.assertEqual(set(role.permissions.values_list('code', flat=True)),
                         {'re.view', 'uw.counterparty.submit'})

    def test_it_does_not_hand_out_binding_or_pricing(self):
        """The whole reason this role exists instead of UNDERWRITING_MANAGER."""
        codes = set(Role.objects.get(code='REINSURER_INTAKE')
                    .permissions.values_list('code', flat=True))
        for danger in ('uw.bind', 'uw.quote', 'uw.pricing.edit',
                       'uw.counterparty.approve',
                       'compliance.counterparty.approve',
                       're.counterparty.approve_ceo'):
            self.assertNotIn(danger, codes, f'intake can also {danger}')

    def test_it_cannot_approve_what_it_raised(self):
        """Raising and approving must stay different people."""
        from core.models import user_has_permission
        self.assertTrue(user_has_permission(self.person, 'uw.counterparty.submit'))
        self.assertFalse(user_has_permission(self.person, 'uw.counterparty.approve'))
        self.assertFalse(user_has_permission(self.person,
                                             'compliance.counterparty.approve'))

    def test_running_it_twice_does_not_double_the_grant(self):
        call_command('seed_reinsurer_intake_role', '--commit',
                     '--emails', self.person.email, verbosity=0)
        self.assertEqual(
            UserRoleAssignment.objects.filter(
                user=self.person, role__code='REINSURER_INTAKE',
                revoked_at__isnull=True).count(), 1)


class IntakeRoleDryRunTests(TestCase):
    """The dry-run must not touch the database.

    Its own class because the class above COMMITS in setUpTestData, and a role
    with a live assignment cannot be deleted (UserRoleAssignment.role is
    PROTECT) - so testing the dry-run there would only prove the delete failed.
    """

    @classmethod
    def setUpTestData(cls):
        call_command('seed_roles', '--skip-bootstrap', verbosity=0)
        cls.person = User.objects.create_user(
            'intaker', 'intaker@alphadirect.co.bw', 'x')

    def test_a_dry_run_changes_nothing(self):
        call_command('seed_reinsurer_intake_role',
                     '--emails', self.person.email, verbosity=0)
        self.assertFalse(Role.objects.filter(code='REINSURER_INTAKE').exists(),
                         'the dry-run created the role')
        self.assertFalse(
            UserRoleAssignment.objects.filter(
                role__code='REINSURER_INTAKE').exists(),
            'the dry-run granted the role')


class IntakeGrantSafetyTests(TestCase):
    """What the grant command must refuse, and what it must leave a trace of.

    Every one of these came from the ship-gate, and every one already had a
    guard in `seed_broker_commission_role` written two days earlier — this
    command was cloned from a May sibling that predated them.
    """

    @classmethod
    def setUpTestData(cls):
        call_command('seed_roles', '--skip-bootstrap', verbosity=0)

    def _run(self, *emails, commit=True):
        args = ['seed_reinsurer_intake_role']
        if commit:
            args.append('--commit')
        if emails:
            args += ['--emails', *emails]
        out = StringIO()
        call_command(*args, stdout=out, verbosity=1)
        return out.getvalue()

    def test_two_accounts_on_one_address_are_granted_to_neither(self):
        """Two active logins on one address is a real production condition.

        Picking whichever row sorts first hands a permission to a person
        nobody chose.
        """
        for n in (1, 2):
            User.objects.create_user(f'twin{n}', 'twin@alphadirect.co.bw', 'x')
        out = self._run('twin@alphadirect.co.bw')
        self.assertIn('AMBIGUOUS', out)
        self.assertFalse(
            UserRoleAssignment.objects.filter(
                role__code='REINSURER_INTAKE').exists(),
            'the role was granted despite two accounts sharing the address')

    def test_the_grant_leaves_an_audit_row(self):
        """With the name list out of the repo this row is the ONLY record of
        who was given this and why."""
        from core.models import AuditLog
        u = User.objects.create_user('solo', 'solo@alphadirect.co.bw', 'x')
        self._run(u.email)
        row = AuditLog.objects.filter(
            table_name='core.UserRoleAssignment').order_by('-id').first()
        self.assertIsNotNone(row, 'the grant was not audited')
        self.assertIn('REINSURER_INTAKE', str(row.new_values))

    def test_a_revoked_person_is_not_quietly_re_granted(self):
        """Somebody took it away on the roles screen. A stale runbook list
        must not hand it back."""
        u = User.objects.create_user('gone', 'gone@alphadirect.co.bw', 'x')
        self._run(u.email)
        asg = UserRoleAssignment.objects.get(user=u, role__code='REINSURER_INTAKE')
        asg.revoked_at = timezone.now()
        asg.save(update_fields=['revoked_at'])

        out = self._run(u.email)
        self.assertIn('WAS REVOKED', out)
        self.assertFalse(
            UserRoleAssignment.objects.filter(
                user=u, role__code='REINSURER_INTAKE',
                revoked_at__isnull=True).exists(),
            'a deliberate revoke was silently undone')

    def test_a_missing_permission_fails_loudly(self):
        """Exiting 0 on a failure reads as success in a runbook."""
        from core.models import Permission
        Permission.objects.filter(code='uw.counterparty.submit').delete()
        with self.assertRaises(CommandError):
            self._run('solo@alphadirect.co.bw')

    def test_a_holder_can_raise_AND_submit_end_to_end(self):
        """Raising a draft nobody can submit is the defect the gate caught
        this morning. Proven here for this role, through the real endpoints."""
        u = User.objects.create_user('e2e', 'e2e@alphadirect.co.bw', 'x')
        self._run(u.email)
        api = APIClient()
        api.force_authenticate(u)

        created = api.post(URL, {'name': 'End To End Re', 'short_code': 'E2ERE'},
                           format='json')
        self.assertEqual(created.status_code, 201, created.data)

        moved = api.post(
            f'/api/v1/reinsurance/counterparties/{created.data["id"]}/transition/',
            {'target': S.PENDING_UW_MANAGER, 'comment': 'ready for review'},
            format='json')
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(moved.data['approval_status'], S.PENDING_UW_MANAGER)
