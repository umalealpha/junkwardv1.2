"""The hard gate on discretionary leave (CFO 2026-09-10).

The CFO's words: *"compassionate, study and other leaves are not protected by
law like sick leave and annual leave ... make other leave hard, ask multiple
questions, minimum 50 words, and that gets approved by me as well ... people
are using this form of leave so they can keep their leave days."*

What must hold, against the real API (no mocks):

  1. Annual and sick are UNCHANGED — no questions, no word count, no CFO.
  2. Compassionate / study / special refuse a bare application, and say every
     missing thing at once.
  3. A 49-word motivation is refused; 50 passes.
  4. A complete application is filed AND parks a CFO-only countersignature, so
     the manager cannot approve it alone.
  5. The CEO cannot sign it — the CFO chose himself alone.
  6. No back-dating, except a death.
  7. Study and special must carry the proof at the door.
"""
import datetime as dt
import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, UserCompanyAccess, UserProfile
from hris import discretionary_leave as dl
from hris import exec_signoff_service
from hris.exec_signoff_models import ExecSignoff
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee

APPLY_URL = '/hris/api/leave-requests/'
POLICY_URL = '/hris/api/leave-policy/'

# 50 words exactly — the floor. Counted the way discretionary_leave.word_count
# counts: runs of letters and digits.
FIFTY_WORDS = ' '.join(f'word{i}' for i in range(50))
FORTY_NINE_WORDS = ' '.join(f'word{i}' for i in range(49))


def _unlock(user):
    p, _ = UserProfile.objects.get_or_create(user=user)
    p.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    p.save(update_fields=['hris_unlocked_until'])
    return p


def _pdf():
    return SimpleUploadedFile('proof.pdf', b'%PDF-1.4 test', content_type='application/pdf')


def _weekday(offset_days: int):
    """A working day roughly `offset_days` away, never a Saturday or Sunday.

    LeaveRequest.save() recomputes `days` in WORKING days, so a fixture pinned
    to today+3 silently becomes a 0-day request on a Thursday — the test then
    passes or fails depending on which day of the week it runs. Walk to a
    weekday in the same direction instead.
    """
    d = timezone.localdate() + dt.timedelta(days=offset_days)
    step = 1 if offset_days >= 0 else -1
    while d.weekday() >= 5:          # 5 = Sat, 6 = Sun
        d += dt.timedelta(days=step)
    return d


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADDL', name='ADIC (discretionary test)')

        cls.staff_user = User.objects.create_user(
            'dlstaff', 'dlstaff@alphadirect.co.bw', 'x',
            first_name='Dolly', last_name='Staff')
        cls.mgr_user = User.objects.create_user(
            'dlmgr', 'dlmgr@alphadirect.co.bw', 'x', first_name='Em', last_name='Manager')
        cls.cfo_user = User.objects.create_user(
            'dlcfo', 'dlcfo@alphadirect.co.bw', 'x', first_name='Cee', last_name='Effo')
        cls.ceo_user = User.objects.create_user(
            'dlceo', 'dlceo@alphadirect.co.bw', 'x', first_name='See', last_name='Eeoh')

        cls.staff_emp = Employee.objects.create(
            company=cls.co, employee_number='DL-1', full_name='Dolly Staff',
            email='dlstaff@alphadirect.co.bw', user=cls.staff_user,
            hire_date=timezone.localdate() - dt.timedelta(days=800))
        cls.mgr_emp = Employee.objects.create(
            company=cls.co, employee_number='DL-2', full_name='Em Manager',
            email='dlmgr@alphadirect.co.bw', user=cls.mgr_user)

        cls.staff_prof = HRISProfile.objects.create(
            employee=cls.staff_emp, manager=cls.mgr_emp)
        cls.mgr_prof = HRISProfile.objects.create(employee=cls.mgr_emp)

        UserProfile.objects.update_or_create(
            user=cls.cfo_user, defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        UserProfile.objects.update_or_create(
            user=cls.ceo_user, defaults={'title': UserProfile.Title.CEO, 'is_active': True})

        for u in (cls.staff_user, cls.mgr_user, cls.cfo_user, cls.ceo_user):
            UserCompanyAccess.objects.get_or_create(user=u, company=cls.co)

        LeaveType.objects.create(code='annual', name='Annual leave',
                                 default_annual_days=21)
        LeaveType.objects.create(code='sick', name='Sick leave',
                                 requires_medical_cert=True)
        cls.compassionate = LeaveType.objects.create(
            code='compassionate', name='Compassionate leave', default_annual_days=5)
        cls.study = LeaveType.objects.create(
            code='study', name='Study leave', default_annual_days=10)

    # A complete, valid compassionate application.
    def good_compassionate(self, **over):
        soon = _weekday(3)
        body = {
            'type': 'compassionate',
            'start_date': soon.isoformat(),
            'end_date': soon.isoformat(),
            'reason_category': 'bereavement',
            'reason': FIFTY_WORDS,
            'policy_ack': 'true',
            'policy_answers': json.dumps({
                'annual_alternative': ' '.join(f'a{i}' for i in range(15)),
                'notice': 'Today',
                'cover': 'Em Manager has agreed',
                'contact': 'Yes',
                'event': 'Death',
                'relationship': 'Parent',
                'person': 'Mma Staff',
                'event_date': (timezone.localdate() - dt.timedelta(days=1)).isoformat(),
                'place': 'Serowe',
                'proof_promise': 'Funeral programme within 7 days',
            }),
        }
        body.update(over)
        return body


# ── 1. statutory leave is untouched ─────────────────────────────────────────

class StatutoryLeaveIsUntouchedTest(Base):
    def test_annual_still_needs_only_a_category(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'end_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'reason_category': 'personal',
        })
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data['needs_exec_signoff'])
        lr = LeaveRequest.objects.get(pk=r.data['id'])
        self.assertEqual(lr.policy_answers, {})
        self.assertFalse(lr.policy_ack)

    def test_the_question_endpoint_says_no_for_annual_and_sick(self):
        self.client.force_authenticate(self.staff_user)
        for code in ('annual', 'sick', 'maternity', 'paternity'):
            r = self.client.get(POLICY_URL, {'type': code})
            self.assertEqual(r.status_code, 200, r.data)
            self.assertFalse(r.data['discretionary'], f'{code} must not be gated')
            self.assertEqual(r.data['questions'], [])

    def test_the_question_endpoint_serves_the_set_for_compassionate(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.get(POLICY_URL, {'type': 'compassionate'})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['discretionary'])
        self.assertEqual(r.data['min_words'], 50)
        keys = {q['key'] for q in r.data['questions']}
        # more than a couple of questions, and the annual-balance challenge is
        # the one that must never quietly disappear
        self.assertGreaterEqual(len(keys), 8)
        self.assertIn('annual_alternative', keys)
        self.assertIn('event', keys)


# ── 2. a bare application is refused, with every gap named ──────────────────

class BareApplicationIsRefusedTest(Base):
    def test_compassionate_with_no_answers_is_refused(self):
        self.client.force_authenticate(self.staff_user)
        soon = _weekday(3)
        r = self.client.post(APPLY_URL, {
            'type': 'compassionate',
            'start_date': soon.isoformat(), 'end_date': soon.isoformat(),
            'reason_category': 'bereavement',
            'reason': 'Funeral.',
        })
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(LeaveRequest.objects.filter(
            profile=self.staff_prof).count(), 0, 'nothing may be filed')
        # every problem at once, not one at a time
        errs = r.data['errors']
        self.assertGreaterEqual(len(errs), 8)
        joined = ' '.join(errs)
        self.assertIn('50 words', joined)
        self.assertIn('remain at work', joined)

    def test_the_acknowledgement_is_not_optional(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, self.good_compassionate(policy_ack='false'))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(any('remain at work' in e for e in r.data['errors']))

    def test_the_annual_balance_question_cannot_be_skipped(self):
        answers = json.loads(self.good_compassionate()['policy_answers'])
        answers.pop('annual_alternative')
        r = self._post_with(answers)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(any('annual leave' in e.lower() for e in r.data['errors']))

    def test_a_one_word_answer_to_a_long_question_is_refused(self):
        answers = json.loads(self.good_compassionate()['policy_answers'])
        answers['annual_alternative'] = 'Because.'
        r = self._post_with(answers)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(any('at least 15 words' in e for e in r.data['errors']))

    def _post_with(self, answers, **over):
        self.client.force_authenticate(self.staff_user)
        body = self.good_compassionate(**over)
        body['policy_answers'] = json.dumps(answers)
        return self.client.post(APPLY_URL, body)


# ── 3. the 50-word floor ────────────────────────────────────────────────────

class FiftyWordFloorTest(Base):
    def test_forty_nine_words_is_refused(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, self.good_compassionate(reason=FORTY_NINE_WORDS))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(any('at least 50 words' in e for e in r.data['errors']),
                        r.data['errors'])
        self.assertTrue(any('You wrote 49' in e for e in r.data['errors']))

    def test_fifty_words_passes(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, self.good_compassionate())
        self.assertEqual(r.status_code, 201, r.data)

    def test_word_count_counts_words_not_spaces(self):
        self.assertEqual(dl.word_count('one   two\t three\nfour'), 4)
        self.assertEqual(dl.word_count("it's a  test"), 3)
        self.assertEqual(dl.word_count('   '), 0)


# ── 4. the CFO countersignature ─────────────────────────────────────────────

class CfoSignoffTest(Base):
    def _apply(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, self.good_compassionate())
        self.assertEqual(r.status_code, 201, r.data)
        return LeaveRequest.objects.get(pk=r.data['id'])

    def test_a_clean_employee_still_gets_a_cfo_signoff(self):
        """No overdue work at all — the CFO signature is about the leave TYPE."""
        lr = self._apply()
        so = ExecSignoff.objects.get(module=ExecSignoff.Module.LEAVE, object_id=lr.pk)
        self.assertEqual(so.kind, ExecSignoff.Kind.LEAVE_POLICY)
        self.assertTrue(so.cfo_only)
        self.assertEqual(so.status, ExecSignoff.Status.PENDING)

    def test_the_applicant_is_told_to_stay_at_work(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, self.good_compassionate())
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data['needs_exec_signoff'])
        msg = r.data['overdue_message']
        self.assertIn('CFO', msg)
        self.assertIn('at work', msg)
        # and NOT the overdue-work wording, which would be a lie
        self.assertNotIn('past the due date', msg)

    def test_the_manager_cannot_approve_until_the_cfo_signs(self):
        lr = self._apply()
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(f'/hris/api/leave-requests/{lr.pk}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 409, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)

    def test_the_ceo_may_not_sign_discretionary_leave(self):
        lr = self._apply()
        so = ExecSignoff.objects.get(object_id=lr.pk)
        self.assertFalse(exec_signoff_service.user_can_sign(self.ceo_user, so),
                         'the CFO chose himself alone as the signer')
        self.client.force_authenticate(self.ceo_user)
        r = self.client.post(f'/hris/api/exec-signoffs/{so.pk}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.data)
        so.refresh_from_db()
        self.assertEqual(so.status, ExecSignoff.Status.PENDING)

    def test_the_ceo_may_still_sign_an_ordinary_overdue_signoff(self):
        """The narrowing must not leak onto the existing overdue gate."""
        lr = LeaveRequest.objects.create(
            profile=self.staff_prof, leave_type=LeaveType.objects.get(code='annual'),
            start_date=timezone.localdate() + dt.timedelta(days=5),
            end_date=timezone.localdate() + dt.timedelta(days=5),
            days=1, status=LeaveRequest.Status.PENDING)
        so = ExecSignoff.objects.create(
            module=ExecSignoff.Module.LEAVE, object_id=lr.pk,
            applicant=self.staff_user, applicant_name='Dolly Staff',
            reason='overdue work')
        self.assertTrue(exec_signoff_service.user_can_sign(self.ceo_user, so))

    def test_the_cfo_signs_and_the_manager_can_then_approve(self):
        lr = self._apply()
        so = ExecSignoff.objects.get(object_id=lr.pk)
        self.client.force_authenticate(self.cfo_user)
        r = self.client.post(f'/hris/api/exec-signoffs/{so.pk}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r2 = self.client.post(f'/hris/api/leave-requests/{lr.pk}/decide/',
                              {'decision': 'approve'}, format='json')
        self.assertEqual(r2.status_code, 200, r2.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)

    def test_the_answers_reach_the_approver_queue(self):
        self._apply()
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.get('/hris/api/leave-requests/queue/')
        self.assertEqual(r.status_code, 200, r.data)
        row = next(x for x in r.data['pending'] if x['leave_code'] == 'compassionate')
        self.assertTrue(row['is_discretionary'])
        self.assertTrue(row['policy_ack'])
        values = ' '.join(a['value'] for a in row['policy_answers'])
        self.assertIn('Serowe', values)
        self.assertEqual(row['policy_history']['count'], 1)


# ── 5. no back-dating, except a death ───────────────────────────────────────

class BackdatingTest(Base):
    def test_a_death_may_be_reported_after_the_fact(self):
        self.client.force_authenticate(self.staff_user)
        past = _weekday(-2)
        r = self.client.post(APPLY_URL, self.good_compassionate(
            start_date=past.isoformat(), end_date=past.isoformat()))
        self.assertEqual(r.status_code, 201, r.data)

    def test_a_non_death_compassionate_cannot_be_back_dated(self):
        answers = json.loads(self.good_compassionate()['policy_answers'])
        answers['event'] = 'Other family emergency'
        self.client.force_authenticate(self.staff_user)
        past = _weekday(-2)
        body = self.good_compassionate(start_date=past.isoformat(),
                                       end_date=past.isoformat())
        body['policy_answers'] = json.dumps(answers)
        r = self.client.post(APPLY_URL, body)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(any('in the past' in e for e in r.data['errors']))

    def test_study_leave_cannot_be_back_dated_at_all(self):
        errors = dl.validate(
            'study',
            answers={q['key']: 'x' for q in dl.questions_for('study', 5)},
            reason=FIFTY_WORDS, ack=True,
            start_date=timezone.localdate() - dt.timedelta(days=1),
            today=timezone.localdate(), annual_available=5, has_document=True)
        self.assertTrue(any('in the past' in e for e in errors), errors)


# ── 6. proof at the door for study / special ────────────────────────────────

class ProofTest(Base):
    def _study_answers(self):
        return {
            'annual_alternative': ' '.join(f'a{i}' for i in range(15)),
            'notice': 'Today', 'cover': 'Em Manager', 'contact': 'Yes',
            'institution': 'UB', 'programme': 'ACCA',
            'assessment': '12-14 October 2026',
            'sponsored': 'I am paying myself',
            'job_link': ' '.join(f'j{i}' for i in range(15)),
            'days_before': 'none',
        }

    def test_study_without_a_document_is_refused(self):
        self.client.force_authenticate(self.staff_user)
        soon = _weekday(10)
        r = self.client.post(APPLY_URL, {
            'type': 'study', 'start_date': soon.isoformat(), 'end_date': soon.isoformat(),
            'reason_category': 'study', 'reason': FIFTY_WORDS, 'policy_ack': 'true',
            'policy_answers': json.dumps(self._study_answers()),
        })
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(any('Proof of study' in e for e in r.data['errors']), r.data['errors'])

    def test_study_with_the_document_passes(self):
        self.client.force_authenticate(self.staff_user)
        soon = _weekday(10)
        r = self.client.post(APPLY_URL, {
            'type': 'study', 'start_date': soon.isoformat(), 'end_date': soon.isoformat(),
            'reason_category': 'study', 'reason': FIFTY_WORDS, 'policy_ack': 'true',
            'policy_answers': json.dumps(self._study_answers()),
            'certificate': _pdf(),
        }, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        so = ExecSignoff.objects.get(object_id=r.data['id'])
        self.assertTrue(so.cfo_only)


# ── 7. the repeat-user report ───────────────────────────────────────────────

class RepeatUserReportTest(Base):
    def test_history_counts_this_type_only(self):
        soon = _weekday(3)
        LeaveRequest.objects.create(
            profile=self.staff_prof, leave_type=self.compassionate,
            start_date=soon, end_date=soon, days=1,
            status=LeaveRequest.Status.APPROVED)
        LeaveRequest.objects.create(
            profile=self.staff_prof, leave_type=self.study,
            start_date=soon + dt.timedelta(days=28),
            end_date=soon + dt.timedelta(days=28), days=1,
            status=LeaveRequest.Status.APPROVED)
        h = dl.history_for(self.staff_prof, 'compassionate')
        self.assertEqual(h['count'], 1)
        self.assertEqual(h['days'], 1.0)

    def test_the_monthly_report_sends_when_there_is_something_to_say(self):
        from django.core import mail
        from django.core.management import call_command
        soon = _weekday(-5)
        LeaveRequest.objects.create(
            profile=self.staff_prof, leave_type=self.compassionate,
            start_date=soon, end_date=soon, days=1,
            status=LeaveRequest.Status.APPROVED)
        mail.outbox = []
        call_command('discretionary_leave_report')
        self.assertEqual(len(mail.outbox), 1, 'the CFO was not sent the list')
        self.assertIn('Discretionary leave', mail.outbox[0].subject)
        self.assertIn('Dolly Staff', mail.outbox[0].body)

    def test_the_monthly_report_stays_quiet_when_there_is_nothing(self):
        from django.core import mail
        from django.core.management import call_command
        mail.outbox = []
        call_command('discretionary_leave_report')
        self.assertEqual(mail.outbox, [], 'an empty monthly mail trains people to ignore it')


# ── 8. belt and braces: no signature row at all ─────────────────────────────

class UnsignedCannotBeApprovedTest(Base):
    """The countersignature row could fail to be written (a bug, an outage).

    If that ever happens the request would sit in the manager's queue looking
    like ordinary leave and the whole control would be off with nobody the
    wiser. Approval asks for a SIGNED countersignature, not merely the absence
    of a pending one.
    """
    def _raw_request(self, leave_type):
        d = _weekday(4)
        return LeaveRequest.objects.create(
            profile=self.staff_prof, leave_type=leave_type,
            start_date=d, end_date=d, days=1,
            status=LeaveRequest.Status.PENDING)

    def test_compassionate_with_no_signoff_row_cannot_be_approved(self):
        lr = self._raw_request(self.compassionate)
        self.assertEqual(ExecSignoff.objects.filter(object_id=lr.pk).count(), 0)
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(f'/hris/api/leave-requests/{lr.pk}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 409, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)

    def test_a_manager_may_still_REFUSE_unsigned_discretionary_leave(self):
        """Declining never needs the CFO — a manager may always say no."""
        lr = self._raw_request(self.compassionate)
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(f'/hris/api/leave-requests/{lr.pk}/decide/',
                             {'decision': 'reject', 'notes': 'Not enough cover.'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.REFUSED)

    def test_annual_leave_with_no_signoff_approves_normally(self):
        lr = self._raw_request(LeaveType.objects.get(code='annual'))
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(f'/hris/api/leave-requests/{lr.pk}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)


# ── 9. the annual balance is frozen onto the record ─────────────────────────

class FrozenAnnualBalanceTest(Base):
    def test_the_balance_at_apply_time_is_stored_and_shown(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, self.good_compassionate())
        self.assertEqual(r.status_code, 201, r.data)
        lr = LeaveRequest.objects.get(pk=r.data['id'])
        self.assertIn(dl.ANNUAL_AT_APPLY_KEY, lr.policy_answers)

        # and the approver reads the question with that FIGURE in it, not a dash
        shown = dl.display_answers('compassionate', lr.policy_answers)
        annual_q = next(a for a in shown if 'annual leave' in a['label'].lower())
        self.assertNotIn('—', annual_q['label'],
                         'the approver must see the number they were holding')


# ── 10. HR cannot apply for someone else's discretionary leave ──────────────

class NotOnSomeoneElsesBehalfTest(Base):
    """The answers, the motivation and the undertaking are the employee's own.

    The on-behalf route exists for people with no login (maternity). HR filling
    it in for a colleague would be HR's words on a control that exists to make
    the EMPLOYEE account for the leave.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # HR-with-amendment-rights is what the on-behalf route requires.
        cls.hr_user = User.objects.create_user(
            'dlhr', 'dlhr@alphadirect.co.bw', 'x', first_name='Aitch', last_name='Arr',
            is_staff=True, is_superuser=True)
        UserCompanyAccess.objects.get_or_create(user=cls.hr_user, company=cls.co)

    def test_hr_is_refused_and_told_why(self):
        from core.hris_access import user_can_amend_hris
        _unlock(self.hr_user)
        self.assertTrue(user_can_amend_hris(self.hr_user),
                        'fixture must actually hold HR amendment rights')
        self.client.force_authenticate(self.hr_user)
        soon = _weekday(3)
        r = self.client.post(APPLY_URL, {
            'on_behalf_employee_id': str(self.staff_emp.pk),
            'type': 'compassionate',
            'start_date': soon.isoformat(), 'end_date': soon.isoformat(),
            'reason_category': 'bereavement', 'reason': FIFTY_WORDS,
        })
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('cannot be applied for on their behalf', r.data['detail'])
        self.assertEqual(LeaveRequest.objects.filter(profile=self.staff_prof).count(), 0)
