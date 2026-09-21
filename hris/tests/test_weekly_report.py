"""Weekly HRIS change report command — CFO directive 2026-06-25.

Senior HR edit HRIS records directly; this report is the oversight trail emailed
to Oprah, the CFO, Kago and Pako.
"""
import datetime as dt
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from hris.amendment_models import HRISAmendment
from hris.management.commands.hris_weekly_change_report import DEFAULT_RECIPIENTS


def _amend(maker, *, status, self_applied, label='Anna Adic', when_days_ago=1):
    a = HRISAmendment.objects.create(
        target_kind=HRISAmendment.Target.EMPLOYEE,
        target_id='emp-1',
        target_label=label,
        changes={'job_title': {'old': 'Intern', 'new': 'Associate', 'label': 'Job title'}},
        status=status,
        maker=maker,
        maker_email=maker.email,
        approver=maker if self_applied else None,
        approver_email=maker.email if self_applied else '',
        decided_at=timezone.now() if status != HRISAmendment.Status.PENDING else None,
    )
    # Backdate created_at into the window (auto_now_add forces now on create).
    HRISAmendment.objects.filter(pk=a.pk).update(
        created_at=timezone.now() - dt.timedelta(days=when_days_ago))
    return a


class WeeklyReportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unami = User.objects.create_user('ubutale', 'ubutale@alphadirect.co.bw', 'x')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_sends_to_the_four_recipients_with_changes(self):
        _amend(self.unami, status=HRISAmendment.Status.APPROVED, self_applied=True)
        _amend(self.unami, status=HRISAmendment.Status.PENDING, self_applied=False, label='Bonolo B')
        call_command('hris_weekly_change_report')
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(set(msg.to), set(DEFAULT_RECIPIENTS))
        self.assertIn('2 change', msg.subject)
        self.assertIn('Anna Adic', msg.body)
        self.assertIn('Associate', msg.body)
        self.assertIn('Applied directly', msg.body)   # self-applied label

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_no_changes_still_sends_zero_report(self):
        call_command('hris_weekly_change_report')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('0 change', mail.outbox[0].subject)
        self.assertIn('No HRIS record changes', mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_window_excludes_older_changes(self):
        _amend(self.unami, status=HRISAmendment.Status.APPROVED, self_applied=True,
               when_days_ago=30)   # outside a 7-day window
        call_command('hris_weekly_change_report', '--days', '7')
        self.assertIn('0 change', mail.outbox[0].subject)

    def test_dry_run_prints_and_does_not_email(self):
        _amend(self.unami, status=HRISAmendment.Status.APPROVED, self_applied=True)
        out = StringIO()
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            call_command('hris_weekly_change_report', '--dry-run', stdout=out)
        self.assertEqual(len(mail.outbox), 0)         # nothing sent
        self.assertIn('Anna Adic', out.getvalue())
        self.assertIn('[dry-run]', out.getvalue())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_to_override(self):
        _amend(self.unami, status=HRISAmendment.Status.APPROVED, self_applied=True)
        call_command('hris_weekly_change_report', '--to', 'a@x.com,b@x.com')
        self.assertEqual(set(mail.outbox[0].to), {'a@x.com', 'b@x.com'})
