"""
hris/tests/test_morning_brief_td_outage.py

Regression test for 2026-09-18: Time Doctor answered 403 at 07:00:16 UTC, the
morning brief hit `raise SystemExit(1)` and simply never arrived. The CFO and
every manager got nothing, and the first anyone knew of it was staff reporting
their own missing hours hours later.

Holding the staff brief stays correct — mailing 95 people a zero-hours report
off a dead feed is worse than mailing nothing. What was wrong is that the hold
was SILENT. It must now announce itself.
"""
from __future__ import annotations

from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from integrations.timedoctor import TimeDoctorError


class MorningBriefTimeDoctorOutageTests(TestCase):

    def test_pull_failure_alerts_instead_of_dying_silently(self):
        """RED before the fix: SystemExit was raised with _alert_breaker untouched."""
        client = mock.MagicMock()
        client.users.side_effect = TimeDoctorError(
            '403 from /api/1.0/users: {"error":"denied"}')

        target = ('hris.management.commands.send_morning_brief.Command._alert_breaker')
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=client), \
             mock.patch(target) as alert:
            # --force only bypasses the unrelated MORNING_BRIEF_ENABLED switch,
            # which is off in an empty test database; it does not touch the
            # Time Doctor path under test.
            #
            # --date pins a WEEKDAY, and that is load-bearing. Without it the
            # command runs against the wall clock, hits its rest-day guard at
            # the weekend ("Rest day (2026-09-20) — no daily brief sent") and
            # returns before it ever reaches the Time Doctor pull. The test
            # then fails with "SystemExit not raised" — not because the alert
            # regressed, but because nothing under test ran at all. Green
            # Monday to Friday, red on Saturday and Sunday: it went red on
            # 19-Sep and blocked every branch behind the hard gate until
            # someone read the stdout. 2026-09-16 is a Wednesday.
            with self.assertRaises(SystemExit):
                call_command('send_morning_brief', force=True, date='2026-09-16')

        self.assertTrue(
            alert.called,
            'a Time Doctor pull failure must raise the held-brief alert — a silent '
            'SystemExit is what hid the 17-Sep outage for 25 hours')

        why = alert.call_args.args[2] if len(alert.call_args.args) > 2 else ''
        self.assertIn('Time Doctor pull failed', str(why))
        self.assertIn('403', str(why),
                      'the alert must carry the real upstream error so the reader '
                      'knows to check access, not the network')
