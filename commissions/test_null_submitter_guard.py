"""A commission submission with no recorded submitter must FAIL CLOSED.

Bug b6ccaa38 (CFO, 21-Sep-2026): the self-review guard was conditional on
`submitted_by_id` being set, so a NULL submitter skipped the whole check and the
submission sailed through approval. The summary-upload path (importer) submitted
with `None`, so 44 live submissions had no submitter and 43 were signed while the
guard was blind. "A missing answer must never read as a pass." These pin the
fail-closed behaviour; each is written to go RED against the pre-fix code.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from commissions import service
from commissions.models import (CommissionAgent, CommissionGroup,
                                 CommissionSubmission, CommissionSubmissionLine)


def _cfo() -> User:
    u = User.objects.create_user('cfo_cg', 'pganesharajah@alphadirect.co.bw', 'pw')
    return u


def _stage1() -> User:
    # STAGE1 roster resolves 'bokani makosha' by full name.
    u = User.objects.create_user('bok_cg', 'bmakosha@alphadirect.co.bw', 'pw',
                                 first_name='Bokani', last_name='Makosha')
    return u


class NullSubmitterGuard(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.grp = CommissionGroup.objects.get(key='in_house')
        cls.agent = CommissionAgent.objects.create(name='Guarded Agent', group=cls.grp)

    def _sub(self, status, submitted_by=None, period='2026-05'):
        S = CommissionSubmission.Status
        sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label=period,
            status=status, submitted_by=submitted_by)
        CommissionSubmissionLine.objects.create(
            submission=sub, policy_number='POL-1', commission_amount=Decimal('1000'))
        return sub

    # 1 — submit refuses a NULL user
    def test_submit_refuses_null_user(self):
        sub = self._sub(CommissionSubmission.Status.DRAFT)
        with self.assertRaises(ValueError):
            service.submit(sub, None)
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.DRAFT)  # unchanged
        self.assertIsNone(sub.submitted_by_id)

    # 2 — final_approve refuses a submission with no recorded submitter (the live path)
    def test_final_approve_refuses_null_submitter(self):
        sub = self._sub(CommissionSubmission.Status.FINAL_REVIEW, submitted_by=None)
        with self.assertRaises(ValueError) as cm:
            service.final_approve(sub, _cfo(), approve=True)
        self.assertIn('no recorded submitter', str(cm.exception).lower())
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.FINAL_REVIEW)  # not approved

    # 3 — review refuses a NULL submitter at stage 1
    def test_review_refuses_null_submitter(self):
        sub = self._sub(CommissionSubmission.Status.SUBMITTED, submitted_by=None)
        with self.assertRaises(ValueError) as cm:
            service.review(sub, _stage1(), approve=True)
        self.assertIn('no recorded submitter', str(cm.exception).lower())
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.SUBMITTED)  # not advanced

    # 4 — an upload records the uploader
    def test_upload_records_the_uploader(self):
        from commissions import importer
        agent = CommissionAgent.objects.create(name='Uploaded Agent', group=self.grp)
        uploader = _stage1()
        # in-house summary path: one gross line, submitted through the chain
        importer.import_inhouse(
            None, '2026-06', commit=True, submit=True, user=uploader,
            _pairs=[('Uploaded Agent', Decimal('500'))])
        sub = CommissionSubmission.objects.get(agent=agent, period_label='2026-06')
        self.assertEqual(sub.submitted_by_id, uploader.id)

    # 5 — the CLI loader refuses to run without an explicit submitter
    def test_cli_requires_as_user(self):
        with self.assertRaises(CommandError):
            call_command('import_commission_workbooks', '/tmp/none.xlsx',
                         '--period', '2026-06', '--submit', '--commit')

    # 6 — regression (GREEN today): a submission WITH a submitter cannot be
    # approved by that same person. A guard, not a proof of the fix.
    def test_self_submit_still_blocked(self):
        cfo = _cfo()
        sub = self._sub(CommissionSubmission.Status.FINAL_REVIEW, submitted_by=cfo)
        with self.assertRaises(ValueError) as cm:
            service.final_approve(sub, cfo, approve=True)
        self.assertIn('your own', str(cm.exception).lower())
