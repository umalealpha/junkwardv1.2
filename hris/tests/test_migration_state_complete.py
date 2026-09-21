"""Every model must be registered by importing its app's models.py alone.

The bug this guards (H75): a model module reachable only through a view import
registers as a side effect of Django importing the URLconf during system checks.
`manage.py makemigrations` looks fine — but `call_command()` skips system checks
by default, and that is exactly how the test runner invokes `migrate`. In that
path the model is absent and the autodetector reports its table as an unmigrated
DELETE; `makemigrations --skip-checks` would write a migration DROPPING the live
table. This first bit hris/disciplinary_models.py + hris/transfer_models.py on
2026-08-20 (the CLI said "No changes detected" while the test runner saw 49 of
52 hris models, same commit).

The check is repo-wide on purpose: any app can grow this wiring mistake, so the
assertion covers all of them, not just hris.

The subprocess is the point: it is the only way to get an interpreter where the
URLconf has genuinely not been imported. In-process the URLconf is already
loaded by the time any test runs, so the bug is invisible.
"""

import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

MANAGE_PY_DIR = Path(__file__).resolve().parent.parent.parent


class MigrationStateCompleteTests(SimpleTestCase):

    def test_no_phantom_drift_when_system_checks_are_skipped(self):
        proc = subprocess.run(
            [sys.executable, 'manage.py', 'makemigrations',
             '--check', '--dry-run', '--skip-checks'],
            cwd=MANAGE_PY_DIR, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(
            proc.returncode, 0,
            'makemigrations reported changes with system checks skipped. A model '
            'module is not imported by its app\'s models.py and only registers as '
            'a side effect of the URLconf, so a checks-skipped command (the test '
            'runner\'s migrate path) would write a migration DROPPING its table. '
            'Import the missing model module from its models.py (see checklist '
            'H75).\n'
            f'--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}'
        )
