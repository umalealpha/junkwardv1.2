"""
integrations/tests/test_timedoctor_403_guards.py

Regression tests for the 2026-09-17/18 Time Doctor outage.

Time Doctor began answering HTTP 403 {"error":"denied"} at 17-Sep 13:50 UTC.
Nobody was told for 25 hours, because BOTH watchdogs were structurally unable
to speak:

  * check_timedoctor_token read only the JWT `exp` claim. The live token is a
    44-character OPAQUE key with no claims at all, so jwt_expiry() returned
    None and the command printed "SKIPPED" every single day since it was
    written. It had never once been capable of raising an alarm.

  * timedoctor_healthcheck returned early whenever CONSOLIDATED_EMAILS_ENABLED
    was on — so the FAIL heartbeat was suppressed exactly when the consolidated
    brief carrying it was the thing that had broken.

Each test below goes RED when its fix is reverted.
"""
from __future__ import annotations

from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from integrations.timedoctor import TimeDoctorError

_OPAQUE_TOKEN = 'a' * 44          # what production actually holds: no JWT claims


class CheckTimeDoctorTokenLiveProbeTests(TestCase):
    """check_timedoctor_token must alert on a DENIED token, not just an expired one."""

    def _run(self, *, users_side_effect, expiry=None):
        client = mock.MagicMock()
        client.token = _OPAQUE_TOKEN
        client.users.side_effect = users_side_effect
        with mock.patch('integrations.management.commands.check_timedoctor_token'
                        '.TimeDoctorClient.from_settings', return_value=client), \
             mock.patch('integrations.management.commands.check_timedoctor_token'
                        '.jwt_expiry', return_value=expiry):
            call_command('check_timedoctor_token')

    def test_403_denied_sends_an_urgent_alert(self):
        """RED before the fix: the command printed SKIPPED and sent nothing."""
        self._run(users_side_effect=TimeDoctorError(
            '403 from /api/1.0/users: {"error":"denied","message":"You don\'t have permission"}'))

        self.assertEqual(len(mail.outbox), 1,
                         'a 403 from Time Doctor must raise an alert email')
        subject = mail.outbox[0].subject.lower()
        self.assertIn('time doctor', subject)
        self.assertNotIn('expire', subject,
                         'a revoked token is denied access, not an expiry — the '
                         'email must not send the reader hunting for a renewal date')

    def test_denied_token_with_a_readable_expiry_still_alerts(self):
        """RED before 19-Sep-2026: a token carrying a future exp skipped the live
        probe entirely, so a blocked account (the 17-Sep outage) was never seen and
        the hourly infra/timedoctor-watch.sh read 'unclear' for ever."""
        from datetime import timedelta
        from django.utils import timezone
        self._run(users_side_effect=TimeDoctorError('403 from /api/1.0/users: {"error":"denied"}'),
                  expiry=timezone.now() + timedelta(days=90))
        self.assertEqual(len(mail.outbox), 1,
                         'a blocked account must alert even when the token has a readable expiry')

    def test_401_also_alerts(self):
        self._run(users_side_effect=TimeDoctorError('401 from Time Doctor — token invalid.'))
        self.assertEqual(len(mail.outbox), 1)

    def test_healthy_opaque_token_sends_nothing(self):
        """A working key with no exp claim is normal — it must stay quiet."""
        self._run(users_side_effect=None)
        self.assertEqual(len(mail.outbox), 0,
                         'a successful probe must not email anybody')

    def test_never_raises(self):
        """The cron must stay green whatever Time Doctor does."""
        try:
            self._run(users_side_effect=RuntimeError('socket exploded'))
        except Exception as exc:                      # noqa: BLE001
            self.fail(f'check_timedoctor_token must never raise, got {exc!r}')


class HealthcheckBreakGlassTests(TestCase):
    """A FAIL heartbeat must escape the consolidated-email switch."""

    def _run_with(self, worst):
        checks = [mock.MagicMock(name='c', level=worst, detail='x')]
        with mock.patch('integrations.management.commands.timedoctor_healthcheck.run_checks',
                        return_value=(worst, 'as of test', checks, [], 0)):
            call_command('timedoctor_healthcheck')

    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_fail_still_sends_when_consolidated_is_on(self):
        """RED before the fix: it returned before run_checks() and emailed nothing."""
        self._run_with('FAIL')
        self.assertEqual(len(mail.outbox), 1,
                         'a FAILing Time Doctor check must break glass and send, '
                         'because the consolidated brief that normally carries it '
                         'is exactly what a Time Doctor outage kills')

    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_pass_still_stays_quiet_when_consolidated_is_on(self):
        """The fix must not turn the daily PASS heartbeat back on."""
        self._run_with('PASS')
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(CONSOLIDATED_EMAILS_ENABLED=False)
    def test_pass_sends_when_consolidated_is_off(self):
        self._run_with('PASS')
        self.assertEqual(len(mail.outbox), 1)
