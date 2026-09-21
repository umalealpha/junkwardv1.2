"""A bug report can never hold a status the board cannot clear.

The open queue is `exclude(status__in=['resolved', 'wont_fix'])`, so any value
outside the five choices is open forever: no filter matches it and no screen can
clear it. One row reached `status='closed'` and sat on the open board for four
days (kbotana 2026-08-06 — closed on the CFO's own confirmation that the leave
balances were right, so the decision was made and the board never showed it).

The API already rejects an unknown status. This covers every other write path,
because `choices` on a CharField is only enforced by `full_clean()`.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import BugReport


class InvalidStatusCannotBeWrittenTests(TestCase):
    def _report(self, **kw):
        return BugReport.objects.create(
            description='x' * 60, word_count=60, screenshot_count=3,
            reporter_email='tester@example.invalid', **kw)

    def test_the_five_real_statuses_all_save(self):
        for value, _label in BugReport.Status.choices:
            r = self._report(status=value)
            r.refresh_from_db()
            self.assertEqual(r.status, value)

    def test_a_status_outside_the_choices_is_refused_by_the_database(self):
        """`.save()` does not check `choices`, so without the constraint this
        row is written and then stranded on the open board forever."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._report(status='closed')

    def test_an_invalid_status_cannot_be_introduced_by_an_update(self):
        r = self._report(status=BugReport.Status.IN_PROGRESS)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BugReport.objects.filter(pk=r.pk).update(status='done')

    def test_every_saveable_status_is_either_open_or_clearable(self):
        """The real invariant: no status may be invisible to BOTH queries. If a
        sixth choice is ever added, this fails until the board handles it."""
        cleared = {BugReport.Status.RESOLVED, BugReport.Status.WONT_FIX}
        for value, _ in BugReport.Status.choices:
            r = self._report(status=value)
            in_open = BugReport.objects.exclude(
                status__in=[BugReport.Status.RESOLVED, BugReport.Status.WONT_FIX]
            ).filter(pk=r.pk).exists()
            self.assertEqual(in_open, value not in cleared,
                             f'{value} is neither properly open nor properly cleared')
