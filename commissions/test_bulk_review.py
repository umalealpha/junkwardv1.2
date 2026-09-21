"""commissions/test_bulk_review.py — one-click "approve the clean ones"
(CFO 2026-08-22). bulk_review batches the SAME per-item review(): it must approve
exactly what the reviewer could approve singly, and skip (never force) the rest.

Run inside the prod container with the PG test DB (sqlite dies on a raw-SQL migration):
  env -u COMMISSIONS_STAGE1_EMAILS ... python manage.py test commissions.test_bulk_review
"""
import os
from decimal import Decimal
from unittest import mock

os.environ.setdefault('COMMISSIONS_STAGE2_EMAILS', 'pako@x.co')

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .api_views import CommissionSubmissionViewSet
from .models import CommissionAgent, CommissionGroup, CommissionSubmission, CommissionSubmissionLine

User = get_user_model()
S = CommissionSubmission.Status


def _post(user, ids):
    req = APIRequestFactory().post('/x', {'ids': ids}, format='json')
    force_authenticate(req, user=user)
    return CommissionSubmissionViewSet.as_view({'post': 'bulk_review'})(req)


class BulkReviewTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')
        self.submitter = User.objects.create_user('sub', 'sub@x.co', 'pw')
        self.reviewer = User.objects.create_user('rv', 'rv@x.co', 'pw',
                                                 first_name='Rev', last_name='One')

    def _submitted(self, name, period='2026-08'):
        agent = CommissionAgent.objects.create(name=name, group=self.grp)
        sub = CommissionSubmission.objects.create(
            agent=agent, group=self.grp, period_label=period,
            status=S.SUBMITTED, submitted_by=self.submitter,
            gross_commission=Decimal('100'))
        CommissionSubmissionLine.objects.create(submission=sub, policy_number='P1',
                                                commission_amount=Decimal('100'))
        return sub

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_bulk_approves_all_actionable(self):
        a, b = self._submitted('Agent A'), self._submitted('Agent B')
        resp = _post(self.reviewer, [str(a.id), str(b.id)])
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertCountEqual(resp.data['approved'], [str(a.id), str(b.id)])
        self.assertEqual(resp.data['skipped'], [])
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.status, S.SECOND_REVIEW)   # stage-1 approve advanced it
        self.assertEqual(b.status, S.SECOND_REVIEW)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_separation_of_duties_item_skipped_not_forced(self):
        # a submission that PAYS the reviewer must be skipped, the other approved
        pays_me = self._submitted('Rev One')          # agent name == reviewer full name
        ok = self._submitted('Agent C')
        resp = _post(self.reviewer, [str(pays_me.id), str(ok.id)])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['approved'], [str(ok.id)])
        self.assertEqual(len(resp.data['skipped']), 1)
        self.assertEqual(resp.data['skipped'][0]['id'], str(pays_me.id))
        pays_me.refresh_from_db()
        self.assertEqual(pays_me.status, S.SUBMITTED)  # untouched — never forced

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_unknown_id_skipped(self):
        resp = _post(self.reviewer, ['ffffffff-ffff-ffff-ffff-ffffffffffff'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['approved'], [])
        self.assertEqual(resp.data['skipped'][0]['reason'], 'not found')

    def test_non_reviewer_forbidden(self):
        plain = User.objects.create_user('plain', 'plain@x.co', 'pw')
        a = self._submitted('Agent D')
        resp = _post(plain, [str(a.id)])
        self.assertEqual(resp.status_code, 403)
        a.refresh_from_db()
        self.assertEqual(a.status, S.SUBMITTED)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_empty_selection_is_400(self):
        self.assertEqual(_post(self.reviewer, []).status_code, 400)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_over_cap_is_rejected_not_silently_truncated(self):
        resp = _post(self.reviewer, [str(i) for i in range(201)])
        self.assertEqual(resp.status_code, 400)          # explicit, never a quiet drop
        self.assertIn('200 or fewer', resp.data['detail'])

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_malformed_id_skipped_not_500(self):
        a = self._submitted('Agent E')
        resp = _post(self.reviewer, ['not-a-uuid', str(a.id)])   # bad id must not 500 the batch
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['approved'], [str(a.id)])
        self.assertTrue(any(s['id'] == 'not-a-uuid' for s in resp.data['skipped']))
