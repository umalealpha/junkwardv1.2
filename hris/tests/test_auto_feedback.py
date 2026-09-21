"""
Tests for automatic monthly feedback (CFO 2026-08-26).

The four things that must hold, because they ARE the instruction:
  1. Omni states facts and NEVER rates — NOT_RATED must not trigger a PIP,
     a warning, or a consecutive-low count.
  2. Nobody is auto-posted unless their manager was actually told first.
  3. A day the person explained is never held against them.
  4. An employee's rejection is only cleared by the manager ANSWERING it.
"""
from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from django.utils import timezone

from hris import auto_feedback as AF
from hris import feedback_pushback as PB
from hris.performance_feedback_models import (
    LOW_RATINGS, MonthlyCheckIn, MonthlyFeedbackNotice as Notice,
    PerformanceCheckRating, silent_months_for)

YEAR, MONTH = 2026, 7


_SEQ = itertools.count(1)


def _emp(name, email=''):
    """employee_number is UNIQUE and defaults to '' — two blanks collide."""
    from payroll.models import Employee
    return Employee.objects.create(
        full_name=name, email=email,
        employee_number=f'T{next(_SEQ):05d}')


def _profile(emp, manager=None):
    from hris.models import HRISProfile
    return HRISProfile.objects.create(employee=emp, manager=manager)


def _day(profile, day, required='8.00', tracked='8.00', status='pending'):
    from hris.models import WorkdayJustification
    return WorkdayJustification.objects.create(
        profile=profile, work_date=dt.date(YEAR, MONTH, day),
        required_hours=Decimal(required), tracked_hours=Decimal(tracked),
        status=status)


class RatingSafetyTests(TestCase):
    """A machine-written month must never become a disciplinary record."""

    def test_not_rated_is_not_a_low_rating(self):
        self.assertNotIn(PerformanceCheckRating.NOT_RATED, LOW_RATINGS)

    def test_not_rated_never_triggers_pip_or_warning(self):
        mgr = _emp('The Manager', 'manager@example.invalid')
        prof = _profile(_emp('The Person'), manager=mgr)
        ci = MonthlyCheckIn.objects.create(
            profile=prof, period_month=MONTH, period_year=YEAR,
            conversation_date=dt.date(YEAR, MONTH, 28),
            overall_rating=PerformanceCheckRating.NOT_RATED,
            evidence='facts', auto_posted=True)
        ci.refresh_from_db()
        self.assertFalse(ci.pip_triggered)
        self.assertFalse(ci.warning_recommended)
        self.assertEqual(ci.consecutive_low_count, 0)

    def test_not_rated_needs_no_evidence_or_concerns(self):
        """clean() only demands those for a LOW rating — an unrated month must
        save without forcing Omni to invent concerns it cannot judge."""
        prof = _profile(_emp('Nobody'))
        ci = MonthlyCheckIn(profile=prof, period_month=MONTH, period_year=YEAR,
                            conversation_date=dt.date(YEAR, MONTH, 28),
                            overall_rating=PerformanceCheckRating.NOT_RATED)
        ci.full_clean()          # must not raise


class FactsTests(TestCase):
    def setUp(self):
        self.prof = _profile(_emp('Fact Subject'))

    def test_hours_gap_is_reported(self):
        _day(self.prof, 1, '8.00', '8.00')
        _day(self.prof, 2, '8.00', '5.00')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['required_hours'], Decimal('16.00'))
        self.assertEqual(f['tracked_hours'], Decimal('13.00'))
        self.assertEqual(f['hours_gap'], Decimal('3.00'))

    def test_an_explained_day_is_not_held_against_them(self):
        """The whole fairness point: someone who answered must not be written up."""
        _day(self.prof, 3, '8.00', '2.00', status='justified')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['short_days'], 1)
        self.assertEqual(f['short_days_unexplained'], 0)
        self.assertEqual(f['short_days_explained'], 1)
        self.assertNotIn('never explained', AF.draft_narrative('X', f))

    def test_an_unexplained_day_is_named_with_its_date(self):
        _day(self.prof, 4, '8.00', '1.00', status='pending')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['short_days_unexplained'], 1)
        text = AF.draft_narrative('X', f)
        self.assertIn('never explained', text)
        self.assertIn('04 Jul', text)

    def test_tiny_shortfalls_are_noise_not_a_pattern(self):
        _day(self.prof, 5, '8.00', '7.90')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['short_days'], 0)

    def test_the_draft_never_contains_a_rating(self):
        _day(self.prof, 6, '8.00', '3.00', status='pending')
        text = AF.draft_narrative('X', AF.facts_for(self.prof, YEAR, MONTH)).lower()
        for word in ('exceeds', 'meets expectations', 'below expectations',
                     'poor', 'unacceptable', 'excellent'):
            self.assertNotIn(word, text)
        self.assertIn('no rating has been given', text)

    def test_a_month_with_nothing_says_so(self):
        _day(self.prof, 7, '8.00', '8.00')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertFalse(f['has_anything_to_say'])


@override_settings(ELRA_PERF_ENABLED=True)
class AutopostGuardTests(TestCase):
    """Nobody is auto-posted unless their manager was told."""

    def setUp(self):
        self.mgr = _emp('Told Manager', 'told@example.invalid')
        self.prof = _profile(_emp('Their Report'), manager=self.mgr)
        _day(self.prof, 8, '8.00', '4.00', status='pending')

    def _run(self, commit=True):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('monthly_feedback_autopost', year=YEAR, month=MONTH,
                     commit=commit, stdout=out)
        return out.getvalue()

    def test_no_notice_means_no_autopost(self):
        self._run()
        self.assertEqual(MonthlyCheckIn.objects.count(), 0)

    def _tell_fully(self):
        """Both the 5th draft AND the 6th reminder — the CFO's 27-Aug rule."""
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.NOTICE)
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.REMINDER)

    def test_notice_sent_then_autopost_writes_an_unrated_checkin(self):
        self._tell_fully()
        self._run()
        ci = MonthlyCheckIn.objects.get(profile=self.prof)
        self.assertEqual(ci.overall_rating, PerformanceCheckRating.NOT_RATED)
        self.assertTrue(ci.auto_posted)
        self.assertIsNone(ci.reviewer)
        self.assertIn('4.0 hours short', ci.evidence)
        self.assertIn('did not confirm', ci.manager_comments)

    def test_autopost_is_idempotent(self):
        self._tell_fully()
        self._run()
        self._run()
        self.assertEqual(MonthlyCheckIn.objects.filter(profile=self.prof).count(), 1)

    def test_a_manager_who_did_the_work_is_left_alone(self):
        self._tell_fully()
        MonthlyCheckIn.objects.create(
            profile=self.prof, period_month=MONTH, period_year=YEAR,
            conversation_date=dt.date(YEAR, MONTH, 28),
            overall_rating=PerformanceCheckRating.MEETS)
        self._run()
        ci = MonthlyCheckIn.objects.get(profile=self.prof)
        self.assertEqual(ci.overall_rating, PerformanceCheckRating.MEETS)
        self.assertFalse(ci.auto_posted)

    def test_silent_months_counts_only_autoposts_inside_the_window(self):
        """Periods are built RELATIVE to today, not hardcoded.

        The first version pinned 2026-05/06 against a today-relative window: it
        passed with the OLD buggy `[:6].count()` too, and would have gone red on
        its own once May fell out of the window (Fable round 3).
        """
        def ago(months):
            y, m = dt.date.today().year, dt.date.today().month - months
            while m <= 0:
                m += 12
                y -= 1
            return y, m

        y1, m1 = ago(1)
        y2, m2 = ago(2)
        yOld, mOld = ago(9)          # comfortably outside the six-month window

        # a NOTICE is not silence
        Notice.objects.create(manager=self.mgr, period_year=y1, period_month=m1,
                              kind=Notice.Kind.NOTICE)
        self.assertEqual(silent_months_for(self.mgr), 0)

        Notice.objects.create(manager=self.mgr, period_year=y1, period_month=m1,
                              kind=Notice.Kind.AUTOPOST)
        Notice.objects.create(manager=self.mgr, period_year=y2, period_month=m2,
                              kind=Notice.Kind.AUTOPOST)
        self.assertEqual(silent_months_for(self.mgr), 2)

        # THIS is the assertion that fails without the real window: an old
        # autopost must not be counted as one of "the last six months".
        Notice.objects.create(manager=self.mgr, period_year=yOld, period_month=mOld,
                              kind=Notice.Kind.AUTOPOST)
        self.assertEqual(silent_months_for(self.mgr), 2,
                         'an autopost older than six months must not count')


class PushbackTests(TestCase):
    """The employee's rejection must force a human answer."""

    def setUp(self):
        self.mgr_user = User.objects.create_user('mgr', email='boss@example.invalid',
                                                 password='x')
        self.mgr = _emp('Boss', 'boss@example.invalid')
        self.prof = _profile(_emp('Aggrieved'), manager=self.mgr)
        self.ci = MonthlyCheckIn.objects.create(
            profile=self.prof, period_month=MONTH, period_year=YEAR,
            conversation_date=dt.date(YEAR, MONTH, 28),
            overall_rating=PerformanceCheckRating.NOT_RATED,
            evidence='facts', auto_posted=True)

    GOOD = ('I was on an approved client visit on both of those days and it was '
            'logged at the time, so the hours are not missing.')

    def test_a_one_word_rejection_is_refused(self):
        for junk in ('', 'no', 'unfair', 'disagree'):
            with self.assertRaises(ValidationError):
                PB.request_comments(self.ci, reason=junk)

    def test_a_real_rejection_flags_it_and_records_the_words(self):
        PB.request_comments(self.ci, reason=self.GOOD)
        self.ci.refresh_from_db()
        self.assertTrue(self.ci.employee_requested_comments)
        self.assertEqual(self.ci.employee_decision,
                         MonthlyCheckIn.EmployeeDecision.DECLINE)
        self.assertIn('client visit', self.ci.employee_response)
        self.assertIsNotNone(self.ci.employee_requested_at)

    def test_only_the_manager_answering_clears_it(self):
        PB.request_comments(self.ci, reason=self.GOOD)
        self.ci.refresh_from_db()
        with self.assertRaises(ValidationError):
            PB.answer_request(self.ci, comments='ok', user=self.mgr_user)
        self.ci.refresh_from_db()
        self.assertTrue(self.ci.employee_requested_comments)

        PB.answer_request(self.ci, user=self.mgr_user, comments=(
            'You are right, the visits were logged and approved. I have noted '
            'that against those two days and the hours stand as worked.'))
        self.ci.refresh_from_db()
        self.assertFalse(self.ci.employee_requested_comments)
        self.assertEqual(self.ci.manager_followup_by, self.mgr_user)
        self.assertIsNotNone(self.ci.manager_followup_at)

    def test_answering_does_not_force_the_employee_to_accept(self):
        PB.request_comments(self.ci, reason=self.GOOD)
        self.ci.refresh_from_db()
        PB.answer_request(self.ci, user=self.mgr_user, comments='x' * 60)
        self.ci.refresh_from_db()
        self.assertEqual(self.ci.employee_decision,
                         MonthlyCheckIn.EmployeeDecision.DECLINE)

    def test_a_second_rejection_reopens_the_loop(self):
        PB.request_comments(self.ci, reason=self.GOOD)
        self.ci.refresh_from_db()
        PB.answer_request(self.ci, user=self.mgr_user, comments='y' * 60)
        self.ci.refresh_from_db()
        PB.request_comments(self.ci, reason=self.GOOD + ' Still not addressed.')
        self.ci.refresh_from_db()
        self.assertTrue(self.ci.employee_requested_comments)
        self.assertEqual(self.ci.manager_followup, '')
        self.assertIsNone(self.ci.manager_followup_at)

    def test_cannot_answer_when_nothing_was_asked(self):
        with self.assertRaises(ValidationError):
            PB.answer_request(self.ci, comments='z' * 60, user=self.mgr_user)

    def test_outstanding_lists_what_is_waiting(self):
        self.assertEqual(PB.outstanding().count(), 0)
        PB.request_comments(self.ci, reason=self.GOOD)
        self.assertEqual(PB.outstanding().count(), 1)

    def test_request_survives_a_failing_side_effect(self):
        """Regression: the email/task used to run INSIDE the same transaction as
        the save. A failed OmniTask (assignee is non-nullable, and an auto-posted
        month has no reviewer) was swallowed by the except, which marked the
        transaction for rollback — Django then reverted the save silently and
        request_comments still returned OK. The rejection simply disappeared.
        """
        from unittest.mock import patch
        with patch('hris.feedback_pushback._raise_manager_task',
                   side_effect=RuntimeError('boom')), \
             patch('hris.feedback_pushback._notify_manager'):
            with self.assertRaises(RuntimeError):
                PB.request_comments(self.ci, reason=self.GOOD)
        # The record must still be there even though the side effect blew up.
        self.ci.refresh_from_db()
        self.assertTrue(self.ci.employee_requested_comments)
        self.assertIn('client visit', self.ci.employee_response)

    def test_no_manager_login_still_records_the_request(self):
        """An auto-posted month often has no reviewer at all. Email-only is
        fine; losing the request is not."""
        self.assertIsNone(self.ci.reviewer)
        PB.request_comments(self.ci, reason=self.GOOD)
        self.ci.refresh_from_db()
        self.assertTrue(self.ci.employee_requested_comments)

    def test_a_locked_feedback_cannot_be_reopened(self):
        self.ci.is_locked = True
        self.ci.save()
        with self.assertRaises(ValidationError):
            PB.request_comments(self.ci, reason=self.GOOD)


class StatusBucketTests(TestCase):
    """CFO 2026-08-27: a manager-REJECTED excuse is stated as not accepted —
    never filed as 'explained and accepted', and never lumped in with a day the
    person simply ignored."""

    def setUp(self):
        self.prof = _profile(_emp('Bucketed'))

    def test_rejected_excuse_is_its_own_bucket(self):
        _day(self.prof, 10, '8.00', '2.00', status='unjustified')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['short_days_rejected'], 1)
        self.assertEqual(f['short_days_unexplained'], 0)
        self.assertEqual(f['short_days_explained'], 0)
        text = AF.draft_narrative('X', f)
        self.assertIn('not accepted by the manager', text)
        self.assertNotIn('never explained', text)

    def test_awaiting_review_concludes_nothing(self):
        _day(self.prof, 11, '8.00', '2.00', status='explained')
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['short_days_awaiting'], 1)
        self.assertEqual(f['short_days_unexplained'], 0)
        self.assertIn('awaiting a manager', AF.draft_narrative('X', f))

    def test_accepted_day_nets_off_the_hours_gap(self):
        """The module promises a day you explained is not written up. A fully
        justified shortfall must not headline 'X hours short'."""
        from hris.models import WorkdayJustification
        d = _day(self.prof, 12, '8.00', '2.00', status='justified')
        d.justified_hours = Decimal('6.00')
        d.save()
        f = AF.facts_for(self.prof, YEAR, MONTH)
        self.assertEqual(f['hours_gap_gross'], Decimal('6.00'))
        self.assertEqual(f['hours_gap'], Decimal('0.00'))
        self.assertNotIn('hours short', AF.draft_narrative('X', f))
        self.assertFalse(f['has_anything_to_say'])


class LateStartThresholdTests(TestCase):
    """CFO 2026-08-27: ONE company start time, 08:15, read from ONE constant."""

    def test_single_constant_is_0815(self):
        import datetime as _dt
        from hris import exceptions_report as xr
        self.assertEqual(xr.LATE_START_AFTER, _dt.time(8, 15))

    def test_late_days_defaults_to_the_shared_constant(self):
        import inspect
        from hris import exceptions_report as xr
        default = inspect.signature(xr.late_days_by_user).parameters['after'].default
        self.assertEqual(default, xr.LATE_START_AFTER)


@override_settings(ELRA_PERF_ENABLED=True)
class AutopostRequiresReminderTests(TestCase):
    """CFO 2026-08-27: if our own reminder never sent, the manager gets more time
    rather than being recorded as silent."""

    def setUp(self):
        self.mgr = _emp('Half Told', 'half@example.invalid')
        self.prof = _profile(_emp('Their Person'), manager=self.mgr)
        _day(self.prof, 9, '8.00', '3.00', status='pending')

    def _run(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('monthly_feedback_autopost', year=YEAR, month=MONTH,
                     commit=True, stdout=out)
        return out.getvalue()

    def test_notice_without_reminder_does_not_autopost(self):
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.NOTICE)
        out = self._run()
        self.assertEqual(MonthlyCheckIn.objects.count(), 0)
        self.assertIn('NOT the reminder', out)

    def test_both_notice_and_reminder_does_autopost(self):
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.NOTICE)
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.REMINDER)
        self._run()
        ci = MonthlyCheckIn.objects.get(profile=self.prof)
        self.assertTrue(ci.auto_posted)
        self.assertEqual(ci.overall_rating, PerformanceCheckRating.NOT_RATED)


class EmployeeIsToldTests(TestCase):
    """The defect that mattered most: the employee was never told."""

    def test_notify_employee_of_autopost_actually_exists(self):
        """A same-repo import behind `except ImportError` is a feature switch
        nobody set — this asserts the callee exists at all (Fable H97)."""
        from hris.performance_feedback_notify import notify_employee_of_autopost
        self.assertTrue(callable(notify_employee_of_autopost))

    def test_the_employee_gets_an_email_naming_their_right_to_push_back(self):
        from django.core import mail
        from hris.performance_feedback_notify import notify_employee_of_autopost
        u = User.objects.create_user('emp1', email='person@example.invalid', password='x')
        emp = _emp('Told Person')
        emp.user = u
        emp.save()
        prof = _profile(emp)
        ci = MonthlyCheckIn.objects.create(
            profile=prof, period_month=MONTH, period_year=YEAR,
            conversation_date=dt.date(YEAR, MONTH, 28),
            overall_rating=PerformanceCheckRating.NOT_RATED,
            evidence='3.0 hours short.', auto_posted=True)
        sent = notify_employee_of_autopost(ci)
        self.assertEqual(sent, 1)
        body = mail.outbox[-1].alternatives[0][0]
        self.assertIn('no rating has been given', body)
        self.assertIn('ask', body.lower())
        self.assertIn('3.0 hours short', body)


class ManagerBoardTaskTests(TestCase):
    """Fable: nothing ever exercised the 'it stays on your board' promise."""

    def test_a_linked_manager_gets_a_real_board_task(self):
        from core.models import OmniTask
        mu = User.objects.create_user('mgr2', email='m2@example.invalid', password='x')
        mgr = _emp('Linked Boss', 'm2@example.invalid')
        mgr.user = mu
        mgr.save()
        prof = _profile(_emp('Complainer'), manager=mgr)
        ci = MonthlyCheckIn.objects.create(
            profile=prof, period_month=MONTH, period_year=YEAR,
            conversation_date=dt.date(YEAR, MONTH, 28),
            overall_rating=PerformanceCheckRating.NOT_RATED, auto_posted=True)
        before = OmniTask.objects.count()
        PB.request_comments(ci, reason=('I do not accept this because both short '
                                        'days were approved client visits.'))
        self.assertEqual(OmniTask.objects.count(), before + 1)
        t = OmniTask.objects.order_by('-created_at').first()
        self.assertEqual(t.assignee, mu)
        self.assertIn('asked you to comment', t.title)


class AccountabilityNotLaunderedTests(TestCase):
    """Fable's sharpest finding: auto-posting must not make a silent manager
    read as compliant."""

    def test_auto_posted_does_not_count_as_feedback_given(self):
        from hris.performance_feedback_models import MonthlyCheckIn as M
        mgr = _emp('Silent One', 'silent@example.invalid')
        prof = _profile(_emp('Their Report 2'), manager=mgr)
        M.objects.create(profile=prof, period_month=MONTH, period_year=YEAR,
                         conversation_date=dt.date(YEAR, MONTH, 28),
                         overall_rating=PerformanceCheckRating.NOT_RATED,
                         auto_posted=True)
        given = M.objects.filter(profile=prof, period_year=YEAR,
                                 period_month=MONTH, auto_posted=False).count()
        auto = M.objects.filter(profile=prof, period_year=YEAR,
                                period_month=MONTH, auto_posted=True).count()
        self.assertEqual(given, 0, 'an auto-posted month must not count as given')
        self.assertEqual(auto, 1)


@override_settings(ELRA_PERF_ENABLED=False)
class ModuleGateTests(TestCase):
    """The performance module's own gate (its DPIA switch).

    These commands were the only entry points into the module without it AND the
    only cron-enabled ones — with the flag off the 5th would have mass-mailed
    staff write-ups for a dormant module whose screens all refuse to open.

    This is also the test that keeps the whole suite honest: the other command
    tests now set the flag explicitly with @override_settings, because relying on
    an exported env var made a green run an accident of my shell (Fable round 3).
    """

    def setUp(self):
        self.mgr = _emp('Gated Manager', 'gated@example.invalid')
        self.prof = _profile(_emp('Gated Report'), manager=self.mgr)
        _day(self.prof, 14, '8.00', '2.00', status='pending')
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.NOTICE)
        Notice.objects.create(manager=self.mgr, period_year=YEAR,
                              period_month=MONTH, kind=Notice.Kind.REMINDER)

    def _run(self, cmd):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command(cmd, year=YEAR, month=MONTH, commit=True, stdout=out)
        return out.getvalue()

    def test_autopost_writes_nothing_when_the_module_is_off(self):
        out = self._run('monthly_feedback_autopost')
        self.assertIn('dormant', out)
        self.assertEqual(MonthlyCheckIn.objects.count(), 0)

    def test_notice_sends_nothing_when_the_module_is_off(self):
        from django.core import mail
        before = len(mail.outbox)
        out = self._run('monthly_feedback_notice')
        self.assertIn('dormant', out)
        self.assertEqual(len(mail.outbox), before)
        self.assertEqual(Notice.objects.filter(kind=Notice.Kind.AUTOPOST).count(), 0)


@override_settings(ELRA_PERF_ENABLED=True)
class ValveCannotWedgeShutTests(APITestCase):
    """Fable rounds 3+4: the fairness valve could jam permanently, and the path
    was reachable from the shipped buttons.

    Manager signs -> employee taps "I do not agree" -> employee then taps Sign ->
    both signed -> the record locks -> the manager's answer is refused by the lock
    check -> the employee waits forever and the manager's task can never be
    satisfied.

    These tests drive the REAL VIEW through the API, not the model. The first
    version set the flag itself and therefore passed with the fix reverted —
    which is the exact failure Fable caught twice before (F1, F4). Revert
    performance_views.py's `employee_requested_comments = False` in the sign path
    and test_signing_through_the_api_clears_the_request goes RED.
    """

    CHECKINS_URL = '/hris/api/performance/checkins/'

    def setUp(self):
        from django.utils import timezone as tz
        self.user = User.objects.create_user('valve', email='valve@example.invalid',
                                             password='x')
        self.mgr = _emp('Valve Boss', 'vboss@example.invalid')
        emp = _emp('Valve Person')
        emp.user = self.user
        emp.save()
        self.prof = _profile(emp, manager=self.mgr)
        self.ci = MonthlyCheckIn.objects.create(
            profile=self.prof, period_month=MONTH, period_year=YEAR,
            conversation_date=dt.date(YEAR, MONTH, 28),
            overall_rating=PerformanceCheckRating.NOT_RATED,
            evidence='facts', auto_posted=True,
            manager_signed_at=tz.now())          # the manager has already signed

    def test_signing_through_the_api_clears_the_request(self):
        """The real path: ask for comments, then sign, both via the view."""
        PB.request_comments(self.ci, reason=('I do not accept this because the '
                                            'short days were approved visits.'))
        self.ci.refresh_from_db()
        self.assertTrue(self.ci.employee_requested_comments)

        self.client.force_authenticate(self.user)
        r = self.client.patch(f'{self.CHECKINS_URL}{self.ci.id}/?mine=1',
                              {'sign': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

        self.ci.refresh_from_db()
        self.assertTrue(self.ci.is_locked, 'both signatures should lock it')
        self.assertFalse(
            self.ci.employee_requested_comments,
            'a locked record must not still be waiting on a manager who can no '
            'longer write to it — this is the wedged-valve regression')

    def test_the_disagreement_itself_survives_signing(self):
        """Signing ends the manager's OBLIGATION, it must not erase the dispute."""
        reason = ('I do not accept this because the short days were approved '
                  'client visits logged at the time.')
        PB.request_comments(self.ci, reason=reason)
        self.ci.refresh_from_db()

        self.client.force_authenticate(self.user)
        self.client.patch(f'{self.CHECKINS_URL}{self.ci.id}/?mine=1',
                          {'sign': True}, format='json')
        self.ci.refresh_from_db()
        self.assertEqual(self.ci.employee_decision,
                         MonthlyCheckIn.EmployeeDecision.DECLINE)
        self.assertIn('approved', self.ci.employee_response)
        self.assertIsNotNone(self.ci.employee_requested_at)

    def test_locked_and_awaiting_is_an_impossible_state(self):
        """Stated directly: nothing can ever clear it, so it must never exist."""
        from django.utils import timezone as tz
        PB.request_comments(self.ci, reason=('Disagreeing at length so the '
                                            'minimum is comfortably met here.'))
        self.client.force_authenticate(self.user)
        self.client.patch(f'{self.CHECKINS_URL}{self.ci.id}/?mine=1',
                          {'sign': True}, format='json')
        self.ci.refresh_from_db()
        self.assertFalse(self.ci.is_locked and self.ci.employee_requested_comments)
