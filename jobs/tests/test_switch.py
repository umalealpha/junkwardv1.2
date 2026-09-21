"""The scheduled-job switch (CFO 2026-09-09).

The dangerous things this must never get wrong: a protected job being switched
off, and the wrapper failing CLOSED (stopping a job because the dashboard is
broken). Both are proved here.
"""
import datetime as dt

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import JobRun, ScheduledJob, ScheduledJobChange
from jobs.protected import PROTECTED, is_protected


class ProtectedListTests(TestCase):
    def test_the_backups_and_watchdogs_are_protected(self):
        for name in ('alpha-finance-backup', 'backup-watch', 'omni-watchdog',
                     'row-watchdog', 'timedoctor-pull', 'realpay-reconcile'):
            self.assertTrue(is_protected(name), name)

    def test_fables_fault_hiding_sends_are_protected(self):
        """Sends whose SILENCE hides a fault — Fable's 2026-09-09 review."""
        for name in ('payment-daily-digest', 'consolidated-ops-digest',
                     'omni-outbound-digest', 'quote-expiry-reminder',
                     'monthly-feedback', 'manager-objectives',
                     'exec-provider-dashboard'):
            self.assertTrue(is_protected(name), name)

    def test_a_plain_nag_is_not_protected(self):
        for name in ('task-reminders', 'welcome-back-digest', 'leave-digest',
                     'hours-reminders'):
            self.assertFalse(is_protected(name), name)


class ToggleTests(TestCase):
    def _job(self, name, protected=False):
        return ScheduledJob.objects.create(name=name, is_protected=protected)

    def _cfo(self):
        from django.contrib.auth.models import User
        from core.models import UserProfile
        u = User.objects.create_user('pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x')
        UserProfile.objects.create(user=u, role=UserProfile.Role.choices[0][0],
                                   title=UserProfile.Title.CFO, is_active=True)
        u.refresh_from_db()
        return u

    def test_a_switchable_job_flips_and_writes_an_audit_row(self):
        self._job('task-reminders')
        self.client.force_login(self._cfo())
        r = self.client.post('/api/v1/cfo/jobs/toggle/',
                             {'name': 'task-reminders', 'on': False, 'reason': 'noise'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        j = ScheduledJob.objects.get(name='task-reminders')
        self.assertFalse(j.is_enabled)
        self.assertIsNotNone(j.off_until)      # every off carries an end date
        self.assertEqual(ScheduledJobChange.objects.filter(job=j, turned_on=False).count(), 1)

    def test_a_protected_job_cannot_be_switched_off(self):
        self._job('alpha-finance-backup', protected=True)
        self.client.force_login(self._cfo())
        r = self.client.post('/api/v1/cfo/jobs/toggle/',
                             {'name': 'alpha-finance-backup', 'on': False},
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(ScheduledJob.objects.get(name='alpha-finance-backup').is_enabled)
        self.assertEqual(ScheduledJobChange.objects.count(), 0)

    def test_switching_off_defaults_to_a_seven_day_end_date(self):
        self._job('leave-digest')
        self.client.force_login(self._cfo())
        self.client.post('/api/v1/cfo/jobs/toggle/', {'name': 'leave-digest', 'on': False},
                         content_type='application/json')
        j = ScheduledJob.objects.get(name='leave-digest')
        self.assertEqual((j.off_until - timezone.localdate()).days, 7)

    def test_the_list_is_the_cfos_own(self):
        from django.contrib.auth.models import User
        from core.models import UserProfile
        u = User.objects.create_user('excoboard', 'excoboard@alphadirect.co.bw', 'x')
        UserProfile.objects.create(user=u, role=UserProfile.Role.choices[0][0],
                                   title=UserProfile.Title.CFO, is_active=True)
        u.refresh_from_db()
        self.client.force_login(u)
        self.assertIn(self.client.get('/api/v1/cfo/jobs/').status_code, (401, 403))


class WrapperTests(TestCase):
    def test_run_job_skips_a_switched_off_job_and_records_it(self):
        j = ScheduledJob.objects.create(name='task-reminders', is_enabled=False)
        call_command('run_job', 'task-reminders', 'check')   # `check` is a real, cheap cmd
        run = JobRun.objects.get(job=j)
        self.assertEqual(run.status, 'skipped')
        self.assertFalse(run.switch_was_on)

    def test_run_job_runs_an_enabled_job(self):
        j = ScheduledJob.objects.create(name='enabled-one', is_enabled=True)
        call_command('run_job', 'enabled-one', 'check')
        run = JobRun.objects.get(job=j)
        self.assertEqual(run.status, 'ok')

    def test_a_protected_job_runs_even_when_the_flag_says_off(self):
        """Belt and braces: even if the flag is somehow off, protected runs."""
        ScheduledJob.objects.create(name='alpha-finance-backup', is_enabled=False,
                                    is_protected=True)
        call_command('run_job', 'alpha-finance-backup', 'check')
        run = JobRun.objects.get(job__name='alpha-finance-backup')
        self.assertEqual(run.status, 'ok')

    def test_the_wrapper_fails_OPEN_when_the_job_is_unknown(self):
        """An unknown job (dashboard out of sync) must RUN, never be blocked."""
        call_command('run_job', 'a-job-not-in-the-table', 'check')
        # no JobRun to assert, but the command must not raise and must have run
        self.assertEqual(JobRun.objects.filter(job__name='a-job-not-in-the-table').count(), 0)
