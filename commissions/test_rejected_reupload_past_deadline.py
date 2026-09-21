"""Bokani Makosha 2026-09-17: a REJECTED commission submission must reopen for
re-upload and re-submit even after the 16th-of-month deadline — the rejection is
what forced the fix, and the agent has no other path to correct it."""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from .api_views import CommissionSubmissionViewSet
from .models import (
    CommissionAgent, CommissionGroup, CommissionSubmission, CommissionSubmissionLine,
)

User = get_user_model()


@override_settings(SUBMISSION_DEADLINE_ENFORCED=True)
class RejectedReuploadPastDeadlineTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.grp = CommissionGroup.objects.get(key='in_house')
        self.agent = CommissionAgent.objects.create(
            name='Bokani Test', group=self.grp, email='reupload@x.co')
        self.user = User.objects.create_user('reupload', 'reupload@x.co', 'pw')
        # A rejected submission exists for THIS agent + month.
        self.sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-09',
            status=CommissionSubmission.Status.REJECTED)
        CommissionSubmissionLine.objects.create(
            submission=self.sub, policy_number='P1', client_name='C',
            transaction_type='new_business', commission_amount=Decimal('500.00'))

    def _past_deadline(self):
        # 17th — past the 16th cutoff. window_open() reads timezone.localdate().
        return mock.patch('core.payroll_deadline._today',
                          lambda: _dt.date(2026, 9, 17))

    def test_submit_rejected_after_deadline_is_allowed(self):
        view = CommissionSubmissionViewSet.as_view({'post': 'submit'})
        with self._past_deadline():
            req = self.factory.post(f'/x/{self.sub.id}/submit')
            force_authenticate(req, user=self.user)
            resp = view(req, pk=str(self.sub.id))
        self.assertNotEqual(resp.status_code, 403, getattr(resp, 'data', resp))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, CommissionSubmission.Status.SUBMITTED)

    def test_submit_draft_after_deadline_is_still_blocked(self):
        # A first-time DRAFT must still see the deadline — the fix is narrow.
        self.sub.status = CommissionSubmission.Status.DRAFT
        self.sub.save()
        view = CommissionSubmissionViewSet.as_view({'post': 'submit'})
        with self._past_deadline():
            req = self.factory.post(f'/x/{self.sub.id}/submit')
            force_authenticate(req, user=self.user)
            resp = view(req, pk=str(self.sub.id))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get('code'), 'deadline_passed')

    def test_reupload_then_submit_after_deadline_is_allowed(self):
        # Bokani Makosha, bug e77ee875 (2026-09-18): the upload itself was let
        # through, but it flipped the sheet to DRAFT — so the agent's very next
        # "I approve" was refused as a first-time submit past the 16th.
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = CommissionSubmissionViewSet.as_view({'post': 'upload'})
        submit = CommissionSubmissionViewSet.as_view({'post': 'submit'})
        parsed = [{'policy_number': 'P2', 'client_name': 'C2',
                   'transaction_type': 'new_business',
                   'commission_amount': Decimal('750.00')}]
        with self._past_deadline(), \
                mock.patch('commissions.access.agent_for_user', lambda u: self.agent), \
                mock.patch('commissions.api_views.agent_for_user', lambda u: self.agent), \
                mock.patch('commissions.importer.parse_workbook', lambda p: parsed):
            req = self.factory.post('/x/upload', {
                'file': SimpleUploadedFile('BOKANI TEST COMMISSION.xlsx', b'x'),
                'period': '2026-09', 'own': 'true'}, format='multipart')
            force_authenticate(req, user=self.user)
            resp = upload(req)
            self.assertEqual(resp.status_code, 200, getattr(resp, 'data', resp))
            self.sub.refresh_from_db()
            self.assertEqual(self.sub.status, CommissionSubmission.Status.REJECTED)
            req = self.factory.post(f'/x/{self.sub.id}/submit')
            force_authenticate(req, user=self.user)
            resp = submit(req, pk=str(self.sub.id))
        self.assertNotEqual(resp.status_code, 403, getattr(resp, 'data', resp))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, CommissionSubmission.Status.SUBMITTED)
        self.assertEqual(self.sub.gross_commission, Decimal('750.00'))
