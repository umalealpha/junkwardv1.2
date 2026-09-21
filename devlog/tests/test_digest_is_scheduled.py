"""A job nobody runs is not a feature — and the digest is the control itself.

THE BUG THIS EXISTS TO CATCH has hit twice in two days, the same way both times:
the code merged, CI went green, and nothing ever ran it.

  10-Sep  send_late_reminder merged. No cron. Not one nudge was ever sent.
  11-Sep  the CFO's own morning brief: the command existed, the README even
          documented the intended schedule, the schedule was never created.
          It had never run once.

Merging is one delivery path; scheduling is a completely separate one, and only
the first has a test. So these are the second.

They matter most for THIS command. devlog_digest is the thing that tells the CFO
what was claimed done and cannot be proven live — a silent digest is worse than
no digest, because the empty inbox reads as "nothing is wrong".
"""
import re
from pathlib import Path

from django.core.management import get_commands
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
CRON_DIR = ROOT / 'infra' / 'cron'
INSTALLER = ROOT / 'infra' / 'install-crons.sh'

# `manage.py <name>` inside a cron line — the actual job being run.
RUNS_COMMAND = re.compile(r'manage\.py\s+(?:run_job\s+\S+\s+--\s+)?([a-z0-9_]+)')

# Found by this test the day it was written (2026-09-11). These schedules live
# in the repo and the installer does not place them. Six of them were hand-laid
# on the host and are running — which means a rebuilt box loses them silently,
# the exact failure the ENABLED list was created to prevent. The seventh,
# tax-compliance, is not on the host at all: it has never run.
#
# They are listed rather than fixed because moving a job into ENABLED makes the
# installer overwrite the host's copy with the repo's, and whether those two
# agree has not been checked. Resolve them one at a time, shrink this list, and
# never add to it — anything new fails the test above, which is the point.
UNMANAGED = {
    'authority-signature-reminders',
    'frozen-screen-shadow',
    'manus-activity-digest',
    'payment-daily-digest',
    'quote-expiry-reminder',
    'screen-integrity',
    'tax-compliance',          # never scheduled at all — merged 11-Sep, dead
}


def _list(block_name: str) -> set:
    """The names inside ENABLED=( ... ) / DISABLED=( ... ) in the installer."""
    text = INSTALLER.read_text(encoding='utf-8')
    m = re.search(rf'^{block_name}=\(\s*(.*?)^\)', text, re.S | re.M)
    if not m:
        return set()
    return {ln.strip() for ln in m.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith('#')}


class CronFilesAndInstallerAgree(SimpleTestCase):
    def test_every_cron_file_is_either_enabled_or_deliberately_disabled(self):
        """A .cron nobody listed is a schedule that never reaches the host."""
        known = _list('ENABLED') | _list('DISABLED') | _list('EXCLUDED') | UNMANAGED
        orphans = sorted(p.stem for p in CRON_DIR.glob('*.cron')
                         if p.stem not in known)
        self.assertEqual(
            orphans, [],
            'these schedules exist in the repo and the installer never places '
            f'them, so the jobs never run: {orphans}')

    def test_every_enabled_name_has_a_schedule_to_install(self):
        missing = sorted(n for n in _list('ENABLED')
                         if not (CRON_DIR / f'{n}.cron').exists())
        self.assertEqual(
            missing, [],
            f'the installer is told to enable schedules that do not exist: {missing}')

    def test_every_scheduled_command_actually_exists(self):
        """Catches a job renamed or deleted while its schedule kept firing."""
        commands = set(get_commands())
        broken = []
        for cron in sorted(CRON_DIR.glob('*.cron')):
            for line in cron.read_text(encoding='utf-8').splitlines():
                if line.lstrip().startswith('#'):
                    continue
                for name in RUNS_COMMAND.findall(line):
                    if name not in commands:
                        broken.append(f'{cron.stem} -> {name}')
        self.assertEqual(
            broken, [],
            f'scheduled to run commands that no longer exist: {broken}')


class TheDigestIsScheduled(SimpleTestCase):
    """Named on its own, because this one is the control over all the others."""

    def test_it_is_in_the_enabled_list(self):
        self.assertIn('devlog-digest', _list('ENABLED'),
                      'the digest exists but nothing runs it — which is the '
                      'exact failure it was built to report on')

    def test_it_runs_every_day(self):
        line = [ln for ln in (CRON_DIR / 'devlog-digest.cron')
                .read_text(encoding='utf-8').splitlines()
                if 'devlog_digest' in ln and not ln.lstrip().startswith('#')]
        self.assertEqual(len(line), 1, 'expected exactly one schedule line')
        fields = line[0].split()
        # minute hour dom month dow — every day, not weekdays only: work is
        # claimed done at weekends too.
        self.assertEqual(fields[2:5], ['*', '*', '*'], line[0])
