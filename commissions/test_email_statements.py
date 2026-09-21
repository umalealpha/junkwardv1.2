"""commissions/test_email_statements.py — month-close: email each approved agent
their statement (CFO 2026-08-22). The mailer is mocked — no real mail is sent.
"""
import os
from decimal import Decimal
from unittest import mock

os.environ.setdefault('COMMISSIONS_STAGE2_EMAILS', 'pako@x.co')

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from . import service
from .api_views import CommissionSubmissionViewSet
from .models import CommissionAgent, CommissionGroup, CommissionSubmission, CommissionSubmissionLine

User = get_user_model()
S = CommissionSubmission.Status


class EmailStatementsServiceTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')
        self.a_email = CommissionAgent.objects.create(name='Has Email', email='a@x.co', group=self.grp)
        self.a_none = CommissionAgent.objects.create(name='No Email', group=self.grp)

    def _sub(self, agent, status=S.APPROVED, period='2026-08'):
        sub = CommissionSubmission.objects.create(agent=agent, group=self.grp,
                                                  period_label=period, status=status,
                                                  gross_commission=Decimal('100'))
        CommissionSubmissionLine.objects.create(submission=sub, policy_number='P1',
                                                commission_amount=Decimal('100'))
        return sub

    def test_preview_lists_who_would_and_who_has_no_email(self):
        self._sub(self.a_email); self._sub(self.a_none)
        r = service.email_agent_statements(self.grp, '2026-08', commit=False)
        self.assertEqual(r['count'], 1)
        self.assertEqual(r['would_email'], ['Has Email'])
        self.assertEqual(r['no_email'], ['No Email'])

    def test_only_approved_or_paid_are_emailed(self):
        self._sub(self.a_email, status=S.APPROVED)
        # a submitted (in-review) one for another emailed agent must NOT be included
        other = CommissionAgent.objects.create(name='Pending', email='p@x.co', group=self.grp)
        self._sub(other, status=S.SUBMITTED)
        r = service.email_agent_statements(self.grp, '2026-08', commit=False)
        self.assertEqual(r['count'], 1)   # only the approved one

    @mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1)
    def test_commit_sends_one_per_agent_with_email(self, mock_send):
        self._sub(self.a_email); self._sub(self.a_none)
        r = service.email_agent_statements(self.grp, '2026-08', commit=True)
        self.assertEqual(r['emailed'], 1)
        self.assertEqual(r['no_email'], ['No Email'])
        self.assertEqual(mock_send.call_count, 1)
        # the agent's own statement must NOT cc the CFO/EXCO
        self.assertEqual(mock_send.call_args.kwargs.get('cc_cfo'), False)
        self.assertEqual(mock_send.call_args.kwargs.get('to'), ['a@x.co'])

    def test_statement_email_has_no_client_names(self):
        # C5 — the outbound statement must NOT carry policyholder/client names.
        from .notify import _agent_statement_html
        sub = self._sub(self.a_email)
        sub.lines.update(client_name='Very Secret Client Name')
        html = _agent_statement_html(sub)
        self.assertNotIn('Secret Client', html)
        self.assertIn('P1', html)   # policy number + commission still shown (the basis)

    @mock.patch('core.notifications.send_html_with_cfo_cc', side_effect=RuntimeError('smtp down'))
    def test_one_bad_send_does_not_stop_the_rest(self, _m):
        self._sub(self.a_email)
        other = CommissionAgent.objects.create(name='Also', email='b@x.co', group=self.grp)
        self._sub(other)
        r = service.email_agent_statements(self.grp, '2026-08', commit=True)   # must not raise
        self.assertEqual(r['emailed'], 0)
        self.assertCountEqual(r['failed'], ['Has Email', 'Also'])


class EmailStatementsEndpointTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')

    def _call(self, user, data):
        req = APIRequestFactory().post('/x', data, format='json')
        force_authenticate(req, user=user)
        return CommissionSubmissionViewSet.as_view({'post': 'email_statements'})(req)

    # An exporter who is also a reviewer (Bokani/Tlamelo/CFO in reality) — the
    # endpoint is gated like payout_export: IsCommissionsReviewer + can_export.
    @mock.patch.dict(os.environ, {'COMMISSIONS_EXPORT_EMAILS': 'exp@x.co',
                                  'COMMISSIONS_STAGE1_EMAILS': 'exp@x.co'})
    def test_exporter_reviewer_can_preview(self):
        exp = User.objects.create_user('exp', 'exp@x.co', 'pw')
        resp = self._call(exp, {'group': 'in_house', 'period': '2026-08', 'preview': '1'})
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertTrue(resp.data['preview'])

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rev@x.co'})
    def test_reviewer_without_export_forbidden(self):
        # a reviewer who is NOT on the export allowlist cannot email statements
        rev = User.objects.create_user('rev', 'rev@x.co', 'pw')
        resp = self._call(rev, {'group': 'in_house', 'period': '2026-08', 'preview': '1'})
        self.assertEqual(resp.status_code, 403)

    def test_non_reviewer_forbidden(self):
        plain = User.objects.create_user('plain', 'plain@x.co', 'pw')
        resp = self._call(plain, {'group': 'in_house', 'period': '2026-08', 'preview': '1'})
        self.assertEqual(resp.status_code, 403)

    @mock.patch.dict(os.environ, {'COMMISSIONS_EXPORT_EMAILS': 'exp@x.co',
                                  'COMMISSIONS_STAGE1_EMAILS': 'exp@x.co'})
    def test_missing_params_400(self):
        exp = User.objects.create_user('exp', 'exp@x.co', 'pw')
        self.assertEqual(self._call(exp, {'preview': '1'}).status_code, 400)
