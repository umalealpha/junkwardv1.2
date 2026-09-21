"""The CFO requirements register must not let a row claim DONE without proof.

CFO 18-Sep-2026 (instruction files 1-3): "No item may be marked DONE from a code
search or PR description alone ... If any row has no owner, acceptance test or
evidence, stop and report it." The first attempt at this control (#1218) was
announced but never checked in, so this test reads the file itself.
"""
from pathlib import Path
from unittest import TestCase

REGISTER = Path(__file__).resolve().parents[2] / 'docs' / 'requirements-register.md'
COLUMNS = ['ID', 'Requirement', 'File', 'Owner', 'PR / commit', 'Acceptance test',
           'Test evidence', 'Live evidence', 'Status', 'Reason if pending']
STATUSES = {'TODO', 'BUILDING', 'PARTIAL', 'BLOCKED', 'DEFERRED', 'DONE'}


def _rows():
    lines = REGISTER.read_text(encoding='utf-8').splitlines()
    start = lines.index('## Register')
    table = [l for l in lines[start:] if l.startswith('|')]
    header = [c.strip() for c in table[0].strip('|').split('|')]
    body = [[c.strip() for c in l.strip('|').split('|')] for l in table[2:]]
    return header, [dict(zip(header, r)) for r in body], body


class RequirementsRegisterTests(TestCase):
    def test_header_has_every_required_column(self):
        header, _, _ = _rows()
        self.assertEqual(header, COLUMNS)

    def test_every_row_is_complete_enough_to_be_honest(self):
        _, rows, raw = _rows()
        self.assertTrue(rows, 'register is empty')
        ids = set()
        for row, cells in zip(rows, raw):
            rid = row['ID']
            self.assertEqual(len(cells), len(COLUMNS), f'{rid}: wrong column count')
            self.assertNotIn(rid, ids, f'{rid}: duplicate ID')
            ids.add(rid)
            self.assertIn(row['Status'], STATUSES, f'{rid}: unknown status')
            self.assertTrue(row['Owner'], f'{rid}: no owner')
            self.assertTrue(row['Acceptance test'], f'{rid}: no acceptance test')

    def test_done_requires_pr_test_and_live_evidence(self):
        _, rows, _ = _rows()
        for row in rows:
            if row['Status'] != 'DONE':
                continue
            for col in ('PR / commit', 'Test evidence', 'Live evidence'):
                self.assertTrue(row[col], f"{row['ID']}: DONE with no {col}")
