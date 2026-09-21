"""Post-leave reversal — the employee worked days they had booked off.

Ontlametse Mogomotsi, ref AD/HR/IA/2026/001 (bug 800fd702 item 3).
CFO 2026-08-10: *"let it be manager's problem — reversal of zero point five or one
day or even five days."*

So the tests check the few things code must hold, and nothing more:
  * half a day, one day and five days all work — the number is the employee's claim;
  * you cannot claim more than the leave holds, and pending claims count;
  * only the employee can raise it, and nobody decides their own;
  * approving credits the days back and shortens the leave, and the ORIGINAL is
    still readable afterwards;
  * a decline must carry a reason, and the employee is told it.
Whether the days were really worked is the manager's judgement, not a rule here.
"""
import datetime as _dt
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company
from hris.leave_reversal_models import LeaveReversal
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee


class ReversalTests(TestCase):
    _seq = 0

    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC',
                                                  name='Alpha Direct Insurance'))
        self.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual Leave', 'default_annual_days': 21})
        self.employee_user, self.profile = self._person('Worked Through Leave')
        self.manager_user, self.manager_profile = self._person('The Manager')
        # Real line-manager link: HRISProfile.manager -> payroll.Employee.
        self.profile.manager = self.manager_profile.employee
        self.profile.save(update_fields=['manager'])
        # hris_role() grants the manager tier to someone who has direct reports,
        # which is how a real line manager gets approve_team_leave without being on
        # the five-person HRIS whitelist (CFO 2026-08-07). The link above is what
        # makes this user a manager; assert it took, or every 403 below is the
        # fixture's fault and not the code's.
        from hris.feature_views import ROLE_CAPABILITIES, hris_role
        self.assertIn('approve_team_leave',
                      ROLE_CAPABILITIES.get(hris_role(self.manager_user), set()),
                      'the fixture manager must hold the real manager capability')

    def _person(self, name):
        ReversalTests._seq += 1
        User = get_user_model()
        user = User.objects.create_user(
            f'rev-user-{ReversalTests._seq}',
            email=f'rev{ReversalTests._seq}@example.invalid', password='x')
        emp = Employee.objects.create(
            full_name=name, company=self.company, user=user,
            employee_number=f'REV{ReversalTests._seq:03d}',
            email=f'rev{ReversalTests._seq}@example.invalid')
        profile = HRISProfile.objects.create(employee=emp)
        # Re-fetch: `user.employee_record` is a cached reverse one-to-one, and it
        # was read as None before this Employee existed. hris_role() goes through
        # that link to decide 'mgr', so a stale user object looks like a plain
        # employee and every manager call 403s for the fixture's reason, not the
        # code's.
        return get_user_model().objects.get(pk=user.pk), profile

    def _leave(self, days='5.00', profile=None):
        return LeaveRequest.objects.create(
            profile=profile or self.profile, leave_type=self.annual,
            start_date=_dt.date(2026, 8, 3), end_date=_dt.date(2026, 8, 7),
            days=D(days), status=LeaveRequest.Status.APPROVED,
            approver=self.manager_user)

    # ── raising a claim ──────────────────────────────────────────────────────
    def _raise(self, lr, days, reason='I covered the month-end close instead.',
               user=None):
        from hris.leave_reversal_views import create_reversal
        req = APIRequestFactory().post(
            f'/hris/api/leave-requests/{lr.id}/reversals/',
            {'days': str(days), 'reason': reason}, format='json')
        force_authenticate(req, user=user or self.employee_user)
        return create_reversal(req, lr.id)

    def test_half_a_day_can_be_reversed(self):
        r = self._raise(self._leave(), '0.5')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['days'], 0.5)

    def test_one_day_can_be_reversed(self):
        r = self._raise(self._leave(), '1')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['days'], 1.0)

    def test_five_days_can_be_reversed(self):
        r = self._raise(self._leave(), '5')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['days'], 5.0)

    def test_you_cannot_claim_more_than_the_leave_holds(self):
        r = self._raise(self._leave('5.00'), '6')
        self.assertEqual(r.status_code, 400)
        self.assertIn('5', r.data['detail'])

    def test_a_pending_claim_counts_against_what_is_left(self):
        lr = self._leave('5.00')
        self.assertEqual(self._raise(lr, '3').status_code, 201)
        second = self._raise(lr, '3')
        self.assertEqual(second.status_code, 400,
                         'two pending claims must not be able to exceed the leave')

    def test_a_quarter_day_is_refused(self):
        r = self._raise(self._leave(), '0.25')
        self.assertEqual(r.status_code, 400)
        self.assertIn('half', r.data['detail'].lower())

    def test_a_reason_is_required(self):
        r = self._raise(self._leave(), '1', reason='worked')
        self.assertEqual(r.status_code, 400)

    def test_only_the_employee_can_raise_it(self):
        """A manager gets 404, not 403: the view scopes the lookup to the caller's
        own leave, so somebody else's request is simply not there to be found. The
        service check behind it is the belt to that braces."""
        lr = self._leave()
        r = self._raise(lr, '1', user=self.manager_user)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(LeaveReversal.objects.count(), 0)

    def test_the_service_also_refuses_a_claim_on_another_persons_leave(self):
        from django.core.exceptions import ValidationError
        from hris.leave_reversal_service import request_reversal
        lr = self._leave()
        with self.assertRaises(ValidationError) as caught:
            request_reversal(leave_request=lr, user=self.manager_user, days='1',
                             reason='Trying to claim it for somebody else.')
        self.assertIn('only the employee', str(caught.exception).lower())

    def test_unapproved_leave_cannot_be_reversed(self):
        lr = self._leave()
        LeaveRequest.objects.filter(pk=lr.pk).update(
            status=LeaveRequest.Status.PENDING)
        lr.refresh_from_db()
        r = self._raise(lr, '1')
        self.assertEqual(r.status_code, 400)

    # ── deciding ─────────────────────────────────────────────────────────────
    def _decide(self, rev_id, decision, notes='', user=None):
        from hris.leave_reversal_views import decide
        req = APIRequestFactory().post(
            f'/hris/api/leave-reversals/{rev_id}/decide/',
            {'decision': decision, 'notes': notes}, format='json')
        force_authenticate(req, user=user or self.manager_user)
        return decide(req, rev_id)

    def test_approving_credits_the_days_back_and_shortens_the_leave(self):
        lr = self._leave('5.00')
        rev_id = self._raise(lr, '2').data['id']
        r = self._decide(rev_id, 'approve')
        self.assertEqual(r.status_code, 200, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.days, D('3.00'), 'the leave must be 2 days shorter')
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)

    def test_the_original_is_still_readable_after_approval(self):
        lr = self._leave('5.00')
        rev_id = self._raise(lr, '2').data['id']
        r = self._decide(rev_id, 'approve')
        orig = r.data['original_leave']
        self.assertEqual(orig['days_at_request'], 5.0,
                         'what the leave said BEFORE must survive')
        self.assertEqual(orig['days_now'], 3.0)
        self.assertEqual(orig['start_date'], '2026-08-03')

    def test_reversing_every_day_cancels_the_leave(self):
        lr = self._leave('5.00')
        rev_id = self._raise(lr, '5').data['id']
        self._decide(rev_id, 'approve')
        lr.refresh_from_db()
        self.assertEqual(lr.days, D('0.00'))
        self.assertEqual(lr.status, LeaveRequest.Status.CANCELLED)

    def test_declining_needs_a_reason(self):
        rev_id = self._raise(self._leave(), '1').data['id']
        r = self._decide(rev_id, 'decline')
        self.assertEqual(r.status_code, 400)
        self.assertIn('reason', r.data['detail'].lower())

    def test_declining_with_a_reason_leaves_the_leave_alone(self):
        lr = self._leave('5.00')
        rev_id = self._raise(lr, '2').data['id']
        r = self._decide(rev_id, 'decline', notes='You were on the roster as off.')
        self.assertEqual(r.status_code, 200, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.days, D('5.00'), 'a decline must not change the leave')
        self.assertEqual(r.data['decision_notes'], 'You were on the roster as off.')

    def test_nobody_decides_their_own_claim(self):
        rev_id = self._raise(self._leave(), '1').data['id']
        r = self._decide(rev_id, 'approve', user=self.employee_user)
        self.assertEqual(r.status_code, 403)

    def test_a_claim_cannot_be_decided_twice(self):
        lr = self._leave('5.00')
        rev_id = self._raise(lr, '2').data['id']
        self.assertEqual(self._decide(rev_id, 'approve').status_code, 200)
        again = self._decide(rev_id, 'approve')
        self.assertEqual(again.status_code, 400,
                         'a second approval would credit the days twice')
        lr.refresh_from_db()
        self.assertEqual(lr.days, D('3.00'))

    # ── what each side sees ──────────────────────────────────────────────────
    def test_the_manager_sees_the_claim_with_the_original_beside_it(self):
        from hris.leave_reversal_views import pending_reversals
        lr = self._leave('5.00')
        self._raise(lr, '2')
        req = APIRequestFactory().get('/hris/api/leave-reversals/pending/')
        force_authenticate(req, user=self.manager_user)
        r = pending_reversals(req)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 1)
        row = r.data['reversals'][0]
        self.assertEqual(row['employee'], 'Worked Through Leave')
        self.assertEqual(row['original_leave']['days_at_request'], 5.0)

    def test_a_manager_does_not_see_another_managers_team(self):
        from hris.leave_reversal_views import pending_reversals
        self._raise(self._leave('5.00'), '2')
        # A REAL manager of somebody else — give them their own report, or the 403
        # would be "you are not a manager at all" and prove nothing about scoping.
        outsider_user, outsider_profile = self._person('Unrelated Manager')
        _their_user, their_report = self._person('Somebody Elses Report')
        their_report.manager = outsider_profile.employee
        their_report.save(update_fields=['manager'])
        outsider_user = get_user_model().objects.get(pk=outsider_user.pk)
        req = APIRequestFactory().get('/hris/api/leave-reversals/pending/')
        force_authenticate(req, user=outsider_user)
        r = pending_reversals(req)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 0)

    def test_the_employee_sees_their_own_claims_and_what_is_still_claimable(self):
        from hris.leave_reversal_views import my_reversals
        lr = self._leave('5.00')
        self._raise(lr, '2')
        req = APIRequestFactory().get('/hris/api/leave-reversals/mine/')
        force_authenticate(req, user=self.employee_user)
        r = my_reversals(req)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 1)
        offered = {x['id']: x for x in r.data['reversible_leave']}
        self.assertEqual(offered[str(lr.id)]['days_claimable'], 3.0,
                         '5 days less the 2 already claimed')

    # ── the balance actually moves ───────────────────────────────────────────
    def test_the_leave_balance_gains_the_days_back(self):
        from hris.leave_balance import balances_for_profile
        lr = self._leave('5.00')

        def available():
            return next(b for b in balances_for_profile(self.profile)
                        if b['code'] == 'annual')['available']

        before = available()
        rev_id = self._raise(lr, '2').data['id']
        self._decide(rev_id, 'approve')
        self.assertEqual(round(available() - before, 1), 2.0,
                         'two days must come back to the bookable balance')

    # ── the database refuses a reasonless decline even outside the view ──────
    def test_a_reasonless_decline_cannot_be_written_by_a_script(self):
        from django.db import IntegrityError, transaction
        lr = self._leave('5.00')
        rev = LeaveReversal.objects.get(pk=self._raise(lr, '2').data['id'])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LeaveReversal.objects.filter(pk=rev.pk).update(
                    status=LeaveReversal.Status.DECLINED, decision_notes='')


class TimeDoctorEvidenceTests(TestCase):
    """The manager is shown the tracked hours beside the claim.

    CFO 2026-08-10: "it can also compare with timedoctor hours and advise manager."
    It advises; it never decides, and it never blocks a claim.
    """

    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        self.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual Leave', 'default_annual_days': 21})
        U = get_user_model()
        user = U.objects.create_user('td-emp', email='td@example.invalid', password='x')
        emp = Employee.objects.create(full_name='Tracked Person', company=self.company,
                                      user=user, employee_number='TD001')
        self.profile = HRISProfile.objects.create(employee=emp)
        self.lr = LeaveRequest.objects.create(
            profile=self.profile, leave_type=self.annual,
            start_date=_dt.date(2026, 8, 3), end_date=_dt.date(2026, 8, 5),
            days=D('3.00'), status=LeaveRequest.Status.APPROVED)

    def _hours(self, day, tracked):
        from hris.models import WorkdayJustification
        return WorkdayJustification.objects.create(
            profile=self.profile, work_date=day,
            required_hours=D('8.00'), tracked_hours=D(str(tracked)))

    def test_no_time_doctor_data_says_so_rather_than_implying_zero(self):
        from hris.leave_reversal_service import timedoctor_evidence
        ev = timedoctor_evidence(self.lr)
        self.assertFalse(ev['available'])
        self.assertIn('no record', ev['advice'].lower())

    def test_it_counts_the_days_with_real_hours(self):
        from hris.leave_reversal_service import timedoctor_evidence
        self._hours(_dt.date(2026, 8, 3), '7.5')     # worked
        self._hours(_dt.date(2026, 8, 4), '0.2')     # not worked
        self._hours(_dt.date(2026, 8, 5), '6.0')     # worked
        ev = timedoctor_evidence(self.lr)
        self.assertTrue(ev['available'])
        self.assertEqual(ev['days_with_hours'], 2.0)
        self.assertEqual(ev['total_hours'], 13.7)

    def test_a_claim_within_the_hours_reads_as_supported(self):
        from hris.leave_reversal_service import evidence_vs_claim
        self._hours(_dt.date(2026, 8, 3), '7.5')
        self._hours(_dt.date(2026, 8, 4), '8.0')
        note = evidence_vs_claim(self.lr, D('2'))
        self.assertIn('within', note.lower())

    def test_a_claim_beyond_the_hours_is_flagged_to_the_manager(self):
        from hris.leave_reversal_service import evidence_vs_claim
        self._hours(_dt.date(2026, 8, 3), '7.5')
        note = evidence_vs_claim(self.lr, D('3'))
        self.assertIn('more than', note.lower())
        self.assertIn('worth asking', note.lower())

    def test_hours_that_look_like_nothing_do_not_count_as_worked(self):
        from hris.leave_reversal_service import evidence_vs_claim
        self._hours(_dt.date(2026, 8, 3), '0.3')
        note = evidence_vs_claim(self.lr, D('1'))
        self.assertIn('nothing here supports', note.lower())

    def test_the_evidence_never_blocks_a_claim(self):
        """Even with zero hours logged, the claim is accepted and put to the
        manager. Advising is not gatekeeping."""
        from hris.leave_reversal_views import create_reversal
        self._hours(_dt.date(2026, 8, 3), '0.0')
        req = APIRequestFactory().post(
            f'/hris/api/leave-requests/{self.lr.id}/reversals/',
            {'days': '1', 'reason': 'Time Doctor was not running that day.'},
            format='json')
        force_authenticate(req, user=self.profile.employee.user)
        r = create_reversal(req, self.lr.id)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIn('nothing here supports', r.data['evidence_note'].lower())


class OriginalApproverDecidesTests(ReversalTests):
    """Report 2026-08-11, item 1: "requests go to the original approver only".

    `approver_for()` already emailed whoever decided the ORIGINAL leave, but the
    queue, the decide gate and the attachment download all asked a different
    question — "is this one of your direct reports?". So a leave signed off by
    somebody other than the line manager produced this: the original approver got
    the email, opened an empty queue, and was told "this is not one of your team
    members" if they tried anyway, while the line manager who was never notified
    could approve it.
    """

    def _leave_approved_by(self, approver, days='5.00'):
        import datetime as _d
        return LeaveRequest.objects.create(
            profile=self.profile, leave_type=self.annual,
            start_date=_d.date(2026, 8, 3), end_date=_d.date(2026, 8, 7),
            days=D(days), status=LeaveRequest.Status.APPROVED, approver=approver)

    def _pending_for(self, user):
        from hris.leave_reversal_views import pending_reversals
        req = APIRequestFactory().get('/hris/api/leave-reversals/pending/')
        force_authenticate(req, user=user)
        return pending_reversals(req)

    def _decide_as(self, rev_id, user, decision='approve', notes='Agreed.'):
        from hris.leave_reversal_views import decide
        req = APIRequestFactory().post(
            f'/hris/api/leave-reversals/{rev_id}/decide/',
            {'decision': decision, 'notes': notes}, format='json')
        force_authenticate(req, user=user)
        return decide(req, rev_id)

    def setUp(self):
        super().setUp()
        # Somebody who signed the leave off but is NOT this employee's line
        # manager — an EXCO member, or a manager covering for the week.
        self.coverer, self.coverer_profile = self._person('Covering Approver')
        # Give the coverer the manager tier the same way a real one gets it: by
        # having a direct report. Without this the capability gate refuses them
        # for the fixture's reason rather than the code's.
        other, other_profile = self._person('Someone Elses Report')
        other_profile.manager = self.coverer_profile.employee
        other_profile.save(update_fields=['manager'])
        self.coverer = get_user_model().objects.get(pk=self.coverer.pk)

    def test_the_original_approver_sees_the_claim_in_their_queue(self):
        lr = self._leave_approved_by(self.coverer)
        self._raise(lr, '2')
        r = self._pending_for(self.coverer)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 1,
                         'the person who was emailed must be able to see it')

    def test_the_original_approver_can_decide_it(self):
        lr = self._leave_approved_by(self.coverer)
        raised = self._raise(lr, '2')
        rev_id = raised.data['id']
        r = self._decide_as(rev_id, self.coverer)
        self.assertEqual(r.status_code, 200, r.data)

    def test_a_line_manager_who_did_not_approve_the_leave_is_refused(self):
        """"Only" the original approver — the manager who was never told cannot."""
        lr = self._leave_approved_by(self.coverer)
        raised = self._raise(lr, '2')
        r = self._decide_as(raised.data['id'], self.manager_user)
        self.assertEqual(r.status_code, 403, r.data)

    def test_the_refusal_names_who_should_decide(self):
        lr = self._leave_approved_by(self.coverer)
        raised = self._raise(lr, '2')
        r = self._decide_as(raised.data['id'], self.manager_user)
        self.assertIn('Covering Approver', str(r.data),
                      'being told "not your team member" sent people to HR to ask')

    def test_the_line_manager_still_decides_when_no_approver_was_recorded(self):
        """No approver on the leave means nobody to route to — do not strand it."""
        lr = self._leave_approved_by(None)
        raised = self._raise(lr, '2')
        r = self._decide_as(raised.data['id'], self.manager_user)
        self.assertEqual(r.status_code, 200, r.data)

    def test_the_original_approver_can_open_the_attached_proof(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from hris.leave_reversal_service import request_reversal
        from hris.leave_reversal_views import reversal_attachment
        lr = self._leave_approved_by(self.coverer)
        rev = request_reversal(
            leave_request=lr, user=self.employee_user, days=D('2'),
            reason='Worked the month-end close instead of taking these days.',
            attachment=SimpleUploadedFile('proof.png', b'\x89PNG\r\n\x1a\n',
                                          content_type='image/png'))
        req = APIRequestFactory().get(
            f'/hris/api/leave-reversals/{rev.id}/attachment/')
        force_authenticate(req, user=self.coverer)
        r = reversal_attachment(req, rev.id)
        self.assertEqual(r.status_code, 200,
                         'the decider must be able to open the proof they are '
                         'being asked to judge')


class MyApprovalsInboxAgreesTests(OriginalApproverDecidesTests):
    """The inbox the notification email points at must show the same claims.

    Fable review 2026-08-11: the decide gate was fixed while `pending_approvals_for`
    in core/approvals_views.py kept its own hand-copied
    `filter(leave_request__profile__manager__user=...)`. That function feeds My
    Approvals, the Task Dashboard panel, the daily chase email and the approval
    push — so the reversal email told the covering approver "open Omni and go to My
    Approvals", where the count was ZERO, while the line manager saw a count they
    were then refused on at decide. Route, inbox and authority must all agree.
    """

    def _reversal_count(self, user):
        from core.approvals_views import pending_approvals_for
        streams = pending_approvals_for(user)
        rows = streams.get('streams', streams) if isinstance(streams, dict) else streams
        for s in rows:
            if s.get('key') == 'leave_reversal':
                return s.get('count', 0)
        return 0

    def test_the_original_approver_sees_it_in_my_approvals(self):
        lr = self._leave_approved_by(self.coverer)
        self._raise(lr, '2')
        self.assertEqual(
            self._reversal_count(self.coverer), 1,
            'the email sends them to My Approvals — it cannot be empty')

    def test_the_un_notified_line_manager_does_not(self):
        lr = self._leave_approved_by(self.coverer)
        self._raise(lr, '2')
        self.assertEqual(
            self._reversal_count(self.manager_user), 0,
            'a count they would be refused on at decide is worse than no count')
