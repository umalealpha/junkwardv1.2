"""
core/tests/test_backup_landed.py

The off-site copy of the omni database stopped landing on 7 JULY 2026 and nobody
knew for SEVEN WEEKS: the job ran every night, dumped the database, encrypted it,
and had its final push refused by GitHub's 100MB file limit. Nothing looked at
the result, so the artifact looked current and a dry-run passed.

Two halves are guarded here:

  * `manage.py alert_backup_not_landed` — the email half. It must refuse to send
    a blank alert, because an alert that says nothing is how a real warning gets
    ignored.
  * the shell scripts — string guards, deliberately. The point is not to prove
    bash works; it is to make the two decisions of 2026-08-25 impossible to undo
    by accident: the dump is encrypted BEFORE it leaves the machine, and the dead
    GitHub leg is not quietly rescheduled.

The date arithmetic in ops/backup_watch.sh has its own self-test:
    ops/backup_watch.sh --self-test
which is exercised from test_the_watcher_self_test_passes below. It caught a
wrong expected epoch on the first run of this work.
"""
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


def _ops(name: str) -> Path:
    return Path(settings.BASE_DIR) / 'ops' / name


class AlertBackupNotLandedTest(TestCase):

    def test_a_blank_alert_is_refused(self):
        """An alert with no detail reads as noise and trains people to ignore it."""
        with self.assertRaises(CommandError):
            call_command('alert_backup_not_landed', detail='   ')

    def test_the_detail_reaches_the_body(self):
        from io import StringIO
        out = StringIO()
        call_command('alert_backup_not_landed', dry_run=True, stdout=out,
                     detail='has not succeeded for 1176 hours')
        body = out.getvalue()
        self.assertIn('has not succeeded for 1176 hours', body)

    def test_the_body_says_omni_itself_is_fine(self):
        """The CFO reads these on a phone. The first question — is the system
        down? — must be answered in the mail, not by ringing somebody."""
        from io import StringIO
        out = StringIO()
        call_command('alert_backup_not_landed', dry_run=True, stdout=out,
                     detail='no marker file')
        body = out.getvalue()
        self.assertIn('running normally', body)
        self.assertIn('restore', body)

    def test_dry_run_sends_nothing(self):
        from io import StringIO
        from unittest import mock
        with mock.patch('core.notifications.send_html_with_cfo_cc') as send:
            call_command('alert_backup_not_landed', dry_run=True,
                         detail='x', stdout=StringIO())
        self.assertFalse(send.called)

    def test_a_real_run_uses_the_house_mail_path(self):
        from io import StringIO
        from unittest import mock
        with mock.patch('core.notifications.send_html_with_cfo_cc',
                        return_value=1) as send:
            call_command('alert_backup_not_landed', detail='stale',
                         stdout=StringIO())
        self.assertTrue(send.called)
        kwargs = send.call_args.kwargs
        self.assertIn('pganesharajah@alphadirect.co.bw', kwargs['to'])
        self.assertIn('stale', kwargs['text_fallback'])


class BackupScriptGuardTest(TestCase):

    def test_the_off_site_backup_encrypts_before_upload(self):
        s = _ops('backup_s3_daily.sh').read_text()
        self.assertIn('gpg --batch --yes', s)
        # The upload must reference the ENCRYPTED file, never the raw dump.
        self.assertIn('aws s3 cp "${ENC}"', s)
        self.assertNotIn('aws s3 cp "${LOCAL}"', s)

    def test_the_off_site_backup_refuses_to_run_without_the_key(self):
        """The failure that killed the duplicate root job nightly must ABORT —
        never fall back to uploading an unencrypted database."""
        s = _ops('backup_s3_daily.sh').read_text()
        self.assertIn('gpg --list-keys', s)
        self.assertIn('Refusing to run', s)

    def test_the_off_site_backup_refuses_an_implausibly_small_dump(self):
        self.assertIn('that is not a real database',
                      _ops('backup_s3_daily.sh').read_text())

    def test_the_off_site_backup_writes_a_heartbeat(self):
        self.assertIn('last-success.txt', _ops('backup_s3_daily.sh').read_text())

    def test_the_watcher_reads_that_heartbeat(self):
        self.assertIn('last-success.txt', _ops('backup_watch.sh').read_text())

    def test_the_watcher_fails_closed(self):
        """Anything it cannot read must be reported, not passed over."""
        s = _ops('backup_watch.sh').read_text()
        self.assertIn('cannot be read', s)
        self.assertIn('never run since it was set up', s)

    def test_the_watcher_cron_is_actually_installed_by_the_installer(self):
        """H37: shipping a .cron file that infra/install-crons.sh does not list
        means the installer only WARNs and the job is never installed. The
        watcher for a seven-week silent failure must not itself fail silently.
        Caught by Fable, 2026-08-25."""
        from pathlib import Path
        from django.conf import settings
        root = Path(settings.BASE_DIR)
        self.assertTrue((root / 'infra' / 'cron' / 'backup-watch.cron').exists())
        installer = (root / 'infra' / 'install-crons.sh').read_text()
        enabled = installer.split('ENABLED=(', 1)[1].split(')', 1)[0]
        self.assertIn('backup-watch', enabled)

    def test_the_github_leg_is_marked_retired(self):
        s = _ops('backup_daily.sh').read_text()
        self.assertIn('RETIRED 2026-08-25', s)
        self.assertIn('Do not schedule this', s)

    def test_the_100mb_reason_is_written_down_not_just_the_decision(self):
        """A future reader must be able to see WHY the GitHub leg went, or they
        will helpfully switch it back on."""
        self.assertIn('100.00 MB', _ops('backup_daily.sh').read_text())

    def test_the_watcher_self_test_passes(self):
        """Exercises the real date arithmetic rather than asserting on strings.
        Skips where GNU date is unavailable (macOS); prod is Linux."""
        script = _ops('backup_watch.sh')
        probe = subprocess.run(['date', '-u', '-d', '2026-08-25T00:00:01Z', '+%s'],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest('GNU date not available on this host')
        r = subprocess.run(['bash', str(script), '--self-test'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('self-test OK', r.stdout)
