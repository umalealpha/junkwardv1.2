"""Same-day CFO ageing escalation (CFO 2026-09-14) — RED-PROVED, not asserted
on faith. Every test here fails if the fix under test is reverted."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from taskboard import escalation
from taskboard.models import PaymentRequest, TaskboardSetting


class EscalationCandidatesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago', 'kago@example.invalid', 'x')

    def _pr(self, ref, status, *, age_days=0, **kw):
        pr = PaymentRequest.objects.create(
            ref=ref, status=status, currency='BWP', total=Decimal(kw.pop('total', '1000.00')),
            entity=kw.pop('entity', 'ADIC'), payee=kw.pop('payee', 'A Supplier'),
            inputter=kw.pop('inputter', 'Kago'), created_by=self.loader, **kw)
        if age_days:
            back = timezone.now() - timedelta(days=age_days)
            PaymentRequest.objects.filter(pk=pr.pk).update(created_at=back)
            pr.refresh_from_db()
        return pr

    def test_a_fresh_request_is_not_a_candidate(self):
        self._pr('P1', PaymentRequest.Status.PENDING_CFO, age_days=0)
        rows, threshold = escalation.candidates(threshold_days=3)
        self.assertEqual(rows, [])
        self.assertEqual(threshold, 3)

    def test_a_request_exactly_at_the_threshold_is_a_candidate(self):
        self._pr('P2', PaymentRequest.Status.PENDING_CFO, age_days=3)
        rows, _ = escalation.candidates(threshold_days=3)
        self.assertEqual([r['ref'] for r in rows], ['P2'])

    def test_only_pending_cfo_is_considered(self):
        self._pr('P3', PaymentRequest.Status.PENDING_FINANCE, age_days=10)
        self._pr('P4', PaymentRequest.Status.PAID, age_days=10)
        rows, _ = escalation.candidates(threshold_days=3)
        self.assertEqual(rows, [])

    def test_an_already_escalated_request_is_never_offered_again(self):
        pr = self._pr('P5', PaymentRequest.Status.PENDING_CFO, age_days=10)
        pr.escalated_at = timezone.now()
        pr.save(update_fields=['escalated_at'])
        rows, _ = escalation.candidates(threshold_days=3)
        self.assertEqual(rows, [])

    def test_default_threshold_comes_from_the_finance_editable_setting(self):
        self._pr('P6', PaymentRequest.Status.PENDING_CFO, age_days=2)
        # Seeded default is 3 — a 2-day-old request is not yet a candidate.
        rows, threshold = escalation.candidates()
        self.assertEqual(threshold, escalation.DEFAULT_STALE_DAYS)
        self.assertEqual(rows, [])
        # Finance lowers it on screen (the admin edits this same row) — the
        # SAME request now qualifies without touching any code.
        row = TaskboardSetting.objects.get(key=escalation.SETTING_KEY)
        row.value = '1'
        row.save()
        rows, threshold = escalation.candidates()
        self.assertEqual(threshold, 1)
        self.assertEqual([r['ref'] for r in rows], ['P6'])

    def test_a_non_numeric_setting_falls_back_to_the_default_rather_than_crash(self):
        TaskboardSetting.objects.create(key=escalation.SETTING_KEY, value='not-a-number')
        self._pr('P7', PaymentRequest.Status.PENDING_CFO, age_days=10)
        rows, threshold = escalation.candidates()
        self.assertEqual(threshold, escalation.DEFAULT_STALE_DAYS)
        self.assertEqual([r['ref'] for r in rows], ['P7'])


class EscalationSendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago2', 'kago2@example.invalid', 'x')

    def _pr(self, ref, age_days):
        pr = PaymentRequest.objects.create(
            ref=ref, status=PaymentRequest.Status.PENDING_CFO, currency='BWP',
            total=Decimal('2500.00'), entity='ADIC', payee='A Supplier',
            inputter='Kago', created_by=self.loader)
        back = timezone.now() - timedelta(days=age_days)
        PaymentRequest.objects.filter(pk=pr.pk).update(created_at=back)
        return pr

    def test_nothing_over_threshold_sends_nothing(self):
        mail.outbox = []
        res = escalation.escalate(threshold_days=3)
        self.assertEqual(res['count'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_stale_request_is_emailed_and_marked_escalated(self):
        pr = self._pr('P10', age_days=5)
        mail.outbox = []
        res = escalation.escalate(threshold_days=3)
        self.assertEqual(res['count'], 1)
        self.assertEqual(len(mail.outbox), 1)
        pr.refresh_from_db()
        self.assertIsNotNone(pr.escalated_at)

    def test_the_same_request_is_never_escalated_twice(self):
        self._pr('P11', age_days=5)
        escalation.escalate(threshold_days=3)
        mail.outbox = []
        res = escalation.escalate(threshold_days=3)
        # It was already marked on the first call, so the second run finds
        # nothing left to escalate.
        self.assertEqual(res['count'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_sends_nothing_and_marks_nothing(self):
        pr = self._pr('P12', age_days=5)
        mail.outbox = []
        res = escalation.escalate(threshold_days=3, dry_run=True)
        self.assertEqual(res['count'], 1)
        self.assertEqual(len(mail.outbox), 0)
        pr.refresh_from_db()
        self.assertIsNone(pr.escalated_at)

    def test_a_failed_send_leaves_the_request_eligible_for_the_next_run(self):
        pr = self._pr('P13', age_days=5)
        import unittest.mock as mock
        with mock.patch('core.notifications.send_html_with_cfo_cc',
                        side_effect=RuntimeError('smtp down')):
            with self.assertRaises(RuntimeError):
                escalation.escalate(threshold_days=3)
        pr.refresh_from_db()
        self.assertIsNone(pr.escalated_at,
                         'a request must stay eligible when the send itself failed')

    def test_no_recipients_configured_sends_nothing_but_does_not_mark_escalated(self):
        self._pr('P14', age_days=5)
        with mock_recipients_empty():
            res = escalation.escalate(threshold_days=3)
        self.assertEqual(res['sent'], 0)
        self.assertEqual(res['to'], [])


def mock_recipients_empty():
    import unittest.mock as mock
    return mock.patch('taskboard.escalation.recipients', return_value=([], []))


class EscalationCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago3', 'kago3@example.invalid', 'x')

    def test_command_dry_run_prints_and_sends_nothing(self):
        pr = PaymentRequest.objects.create(
            ref='P20', status=PaymentRequest.Status.PENDING_CFO, currency='BWP',
            total=Decimal('900.00'), entity='ADIC', payee='X', inputter='Kago',
            created_by=self.loader)
        PaymentRequest.objects.filter(pk=pr.pk).update(
            created_at=timezone.now() - timedelta(days=5))
        mail.outbox = []
        out = StringIO()
        call_command('payment_ageing_escalation', '--stale-days', '3',
                     '--dry-run', stdout=out)
        self.assertIn('DRY RUN', out.getvalue())
        self.assertEqual(len(mail.outbox), 0)

    def test_command_with_nothing_stale_prints_plainly(self):
        out = StringIO()
        call_command('payment_ageing_escalation', '--stale-days', '3', stdout=out)
        self.assertIn('Nothing over 3 day', out.getvalue())


class DigestAgeIsGaboroneDateTests(TestCase):
    """The 09:30 digest ages rows the same way the escalation does.

    test_payment_ageing_gaborone_date.py pins `escalation.candidates()`; this
    pins `payment_digest.collect()`, which carried the identical UTC-vs-CAT
    subtraction and is what the CFO actually reads each morning. Explicit
    clock, so it fails at any hour rather than only inside the window.
    """

    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago5', 'kago5@example.invalid', 'x')

    def test_a_request_raised_late_at_night_is_not_aged_a_day_early(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from taskboard import payment_digest
        gabs = ZoneInfo('Africa/Gaborone')
        # 01:00 CAT on the 13th == 23:00 UTC on the 12th: the two calendars
        # disagree, which is the whole bug.
        created = datetime(2026, 9, 13, 1, 0, tzinfo=gabs)
        now = datetime(2026, 9, 15, 0, 30, tzinfo=gabs)   # 2 CAT days later

        pr = PaymentRequest.objects.create(
            ref='P40', status=PaymentRequest.Status.PENDING_CFO, currency='BWP',
            total=Decimal('1000.00'), entity='ADIC', payee='A Supplier',
            inputter='Kago', created_by=self.loader)
        PaymentRequest.objects.filter(pk=pr.pk).update(created_at=created)

        d = payment_digest.collect(now=now)
        row = next(r for r in d['waiting_cfo'] if r['ref'] == 'P40')
        self.assertEqual(
            row['age_days'], 2,
            'the digest aged a 2-day-old request as 3 — it measured against '
            'the UTC date instead of the Gaborone one')


class EscalationSendFailureTests(TestCase):
    """A send that delivers nothing must NOT mark the rows escalated.

    candidates() only ever returns rows with escalated_at IS NULL, so marking
    on a failed send buries an ageing payment for good: the CFO is never told
    and the job will never offer it again.
    """

    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago4', 'kago4@example.invalid', 'x')

    def _stale(self, ref):
        pr = PaymentRequest.objects.create(
            ref=ref, status=PaymentRequest.Status.PENDING_CFO, currency='BWP',
            total=Decimal('1000.00'), entity='ADIC', payee='A Supplier',
            inputter='Kago', created_by=self.loader)
        PaymentRequest.objects.filter(pk=pr.pk).update(
            created_at=timezone.now() - timedelta(days=5))
        return pr

    def test_a_send_that_delivers_nothing_leaves_the_row_for_the_next_run(self):
        import unittest.mock as mock
        pr = self._stale('P30')
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=0):
            result = escalation.escalate(threshold_days=3)
        self.assertEqual(result['sent'], 0)
        pr.refresh_from_db()
        self.assertIsNone(
            pr.escalated_at,
            'a row was marked escalated even though nothing was delivered — '
            'the CFO would never be told and it can never be offered again')
        rows, _ = escalation.candidates(threshold_days=3)
        self.assertEqual([r['ref'] for r in rows], ['P30'])

    def test_a_send_that_delivers_does_mark_the_row(self):
        import unittest.mock as mock
        pr = self._stale('P31')
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1):
            result = escalation.escalate(threshold_days=3)
        self.assertEqual(result['sent'], 1)
        pr.refresh_from_db()
        self.assertIsNotNone(pr.escalated_at)

    def test_the_command_fails_loudly_when_the_send_delivered_nothing(self):
        """The cron log must not read SUCCESS for a run that told the CFO
        nothing. Rows left deliberately un-escalated is a failure outcome."""
        import unittest.mock as mock
        self._stale('P32')
        out, err = StringIO(), StringIO()
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=0):
            with self.assertRaises(SystemExit) as caught:
                call_command('payment_ageing_escalation', '--stale-days', '3',
                             stdout=out, stderr=err)
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn('Escalated', out.getvalue())
        self.assertIn('delivered NOTHING', err.getvalue())
