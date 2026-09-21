"""commissions/test_ai_review.py — Aria's on-demand second opinion (CFO
2026-08-22). Read-only, best-effort, never changes data, never sent raw PII.
"""
import os
from decimal import Decimal
from unittest import mock

os.environ.setdefault('COMMISSIONS_STAGE2_EMAILS', 'pako@x.co')

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from . import ai_review
from .api_views import CommissionSubmissionViewSet
from .models import CommissionAgent, CommissionGroup, CommissionSubmission, CommissionSubmissionLine

User = get_user_model()
S = CommissionSubmission.Status


class AiOpinionTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')
        self.agent = CommissionAgent.objects.create(name='Opinion Agent', group=self.grp)

    def _sub(self, gross='100'):
        sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-08',
            status=S.SUBMITTED, gross_commission=Decimal(gross))
        CommissionSubmissionLine.objects.create(submission=sub, policy_number='P1',
                                                amount_applicable=Decimal('1000'),
                                                commission_rate=Decimal('10'),
                                                commission_amount=Decimal(gross))
        return sub

    @mock.patch('core.ai_assist.reasoning_complete', return_value='Looks fine to approve.')
    @mock.patch('core.ai_assist.is_safe_for_ai')
    def test_aria_answers(self, mock_safe, _mock_reason):
        from core.ai_assist import SafetyReport
        mock_safe.return_value = SafetyReport(safe=True, redacted_text='shape', redactions_made=0, notes=[])
        r = ai_review.ai_opinion(self._sub())
        self.assertEqual(r['source'], 'aria')
        self.assertIn('approve', r['note'].lower())

    @mock.patch('core.ai_assist.reasoning_complete', side_effect=RuntimeError('down'))
    def test_falls_back_to_checks_when_ai_down(self, _m):
        r = ai_review.ai_opinion(self._sub())          # clean sheet
        self.assertEqual(r['source'], 'checks')
        self.assertIn('consistent', r['note'].lower())

    def test_no_raw_shape_sent_when_redaction_empty(self):
        from core.ai_assist import SafetyReport
        with mock.patch('core.ai_assist.is_safe_for_ai',
                        return_value=SafetyReport(safe=True, redacted_text='', redactions_made=0, notes=[])), \
             mock.patch('core.ai_assist.reasoning_complete') as mock_reason:
            r = ai_review.ai_opinion(self._sub())
        self.assertEqual(r['source'], 'checks')
        mock_reason.assert_not_called()

    def test_never_raises_and_changes_nothing(self):
        sub = self._sub()
        before = sub.status
        with mock.patch('commissions.review_flags.review_flags', side_effect=RuntimeError('boom')):
            r = ai_review.ai_opinion(sub)              # even if flags blow up
        self.assertIn('note', r)
        sub.refresh_from_db()
        self.assertEqual(sub.status, before)           # read-only


class AiCheckEndpointTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')
        self.agent = CommissionAgent.objects.create(name='EP Agent', group=self.grp)
        self.sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-08',
            status=S.SUBMITTED, gross_commission=Decimal('100'))

    def _call(self, user):
        req = APIRequestFactory().post('/x')
        force_authenticate(req, user=user)
        return CommissionSubmissionViewSet.as_view({'post': 'ai_check'})(req, pk=str(self.sub.id))

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    @mock.patch('core.ai_assist.reasoning_complete', side_effect=RuntimeError('down'))
    def test_reviewer_gets_a_note(self, _m):
        rv = User.objects.create_user('rv', 'rv@x.co', 'pw')
        resp = self._call(rv)
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertIn('note', resp.data)

    def test_non_reviewer_forbidden(self):
        plain = User.objects.create_user('plain', 'plain@x.co', 'pw')
        self.assertEqual(self._call(plain).status_code, 403)
