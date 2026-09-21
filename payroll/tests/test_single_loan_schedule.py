"""ONE loan run a month, and it belongs to the orchestrator.

CFO, payroll.docx §4 (16-Sep-2026): *"the monthly orchestration currently runs
alongside the standalone staff-loan cron at 04:00 UTC on the 20th. Remove the
standalone loan cron from the installer, deployment configuration and production
schedule… add a regression test preventing the duplicate schedule from
returning."*

Both jobs fired `0 4 20 * *`. Same function, same period, nothing locking them
against each other — so whichever lost the race hit the (loan, period) unique
key and reported as a FAILED loan step. A correct system looked broken, every
month, and the real fix kept being hunted in the loan code.

The live file was moved aside by hand on the box. That alone does not hold:
`infra/install-crons.sh` still listed the job, so the next rebuild would have
put the duplicate straight back. These assertions read the installer and the
cron files as they are on disk, so the duplicate cannot return through a merge.

RED-PROOF: move `staff-loan-repayments` from DISABLED back into ENABLED in
infra/install-crons.sh and all three tests fail — the first on the ENABLED
membership, the second on the DISABLED membership, the third because the
standalone cron is live again and a second loan execution path exists.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / 'infra' / 'install-crons.sh'
CRON_DIR = REPO / 'infra' / 'cron'

STANDALONE = 'staff-loan-repayments'
ORCHESTRATOR = 'payroll-monthly-orchestration'
#: The management command that materialises a month's loan deductions. Only the
#: orchestrator may reach it, and it reaches it in Python, not through cron.
LOAN_COMMAND = 'run_staff_loan_repayments'


def _bash_array(name: str) -> list[str]:
    """The entries of a top-level `NAME=( … )` array in the installer.

    Comment lines are dropped, which is the point: the job may be NAMED in a
    comment inside ENABLED without being enabled.
    """
    text = INSTALLER.read_text(encoding='utf-8')
    block = re.search(rf'^{name}=\((.*?)^\)', text, re.S | re.M)
    assert block, f'{name}=( … ) not found in {INSTALLER}'
    out = []
    for raw in block.group(1).splitlines():
        line = raw.split('#', 1)[0].strip()
        if line:
            out.extend(line.split())
    return out


class SingleLoanScheduleTests(SimpleTestCase):

    def test_standalone_loan_cron_is_not_installed(self):
        self.assertNotIn(
            STANDALONE, _bash_array('ENABLED'),
            f'{STANDALONE} is back in the installer ENABLED list. It runs the '
            f'same loan step as {ORCHESTRATOR}, at the same time, with no lock '
            'between them — the loser fails on the (loan, period) unique key '
            'and reports as a broken loan run.')

    def test_standalone_loan_cron_is_actively_disabled(self):
        # Merely leaving it out is not enough: a host that already carries the
        # file keeps running it. DISABLED makes the installer DELETE the active
        # file and leave the .DISABLED marker.
        self.assertIn(
            STANDALONE, _bash_array('DISABLED'),
            f'{STANDALONE} must be in DISABLED, not simply absent, or a box '
            'that already has the file keeps firing it after every rebuild.')

    def test_exactly_one_loan_execution_path_is_scheduled(self):
        enabled = set(_bash_array('ENABLED'))
        self.assertIn(ORCHESTRATOR, enabled,
                      'the orchestrator owns the loan step — it must be enabled')

        callers = sorted(
            path.stem for path in CRON_DIR.glob('*.cron')
            if path.stem in enabled and LOAN_COMMAND in path.read_text(encoding='utf-8')
        )
        self.assertEqual(
            callers, [],
            f'{LOAN_COMMAND} is scheduled directly by {callers}. The loan step '
            f'runs inside {ORCHESTRATOR}; a cron that calls it as well is the '
            'duplicate run this test exists to keep out.')
